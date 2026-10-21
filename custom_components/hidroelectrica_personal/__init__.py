"""Hidroelectrica (personal) — integrare proprie pentru contul iHidro."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from . import parsers as p
from .api import HidroApiClient, HidroApiError, HidroAuthError
from .const import (
    ATTR_ACCOUNT,
    ATTR_FULL,
    ATTR_INDEX,
    ATTR_READINGS,
    ATTR_SECTIONS,
    ATTR_VALIDATE_ONLY,
    CONF_ACCOUNTS,
    CONF_HISTORY_DAYS,
    CONF_SELECTED_ACCOUNTS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PLATFORMS,
    SERVICE_GET_DATA,
    SERVICE_REFRESH,
    SERVICE_SUBMIT_READING,
)
from .coordinator import HidroConfigEntry, HidroCoordinator, HidroRuntimeData
from .reading import async_submit_reading

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

DATA_SECTIONS = ["bill", "window", "pod", "meter", "reads", "usage", "invoices", "payments",
                 "previous_reads", "counter_series"]

_TARGET = {
    vol.Optional(ATTR_ACCOUNT): cv.string,
    vol.Optional("device_id"): vol.Any(cv.string, [cv.string]),
}
SUBMIT_SCHEMA = vol.Schema({
    **_TARGET,
    vol.Optional(ATTR_INDEX): vol.All(vol.Coerce(float), vol.Range(min=0)),
    vol.Optional(ATTR_READINGS): {cv.string: vol.All(vol.Coerce(float), vol.Range(min=0))},
    vol.Optional(ATTR_VALIDATE_ONLY, default=False): cv.boolean,
})
REFRESH_SCHEMA = vol.Schema({**_TARGET, vol.Optional(ATTR_FULL, default=True): cv.boolean})
GET_DATA_SCHEMA = vol.Schema({
    **_TARGET,
    vol.Optional(ATTR_SECTIONS): vol.All(cv.ensure_list, [vol.In(DATA_SECTIONS)]),
})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HidroConfigEntry) -> bool:
    client = HidroApiClient(
        async_get_clientsession(hass), entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
    )
    try:
        await client.async_login()
    except HidroAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except HidroApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    opts = {**entry.data, **entry.options}
    interval = int(opts.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
    history_days = int(opts.get(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS))
    accounts: dict[str, dict] = entry.data.get(CONF_ACCOUNTS, {})
    selected: list[str] = opts.get(CONF_SELECTED_ACCOUNTS) or list(accounts)

    runtime = HidroRuntimeData(client=client)
    for uan in selected:
        runtime.coordinators[uan] = HidroCoordinator(
            hass, entry, client, uan, accounts.get(uan, {}), interval, history_days
        )
    # Primul refresh secvențial evită login-uri paralele și limitările serverului.
    for coord in runtime.coordinators.values():
        await coord.async_config_entry_first_refresh()

    entry.runtime_data = runtime
    _remove_stale_devices(hass, entry, set(selected))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Reîncărcarea după modificarea opțiunilor o face OptionsFlowWithReload.
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HidroConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _remove_stale_devices(hass: HomeAssistant, entry: HidroConfigEntry, keep: set[str]) -> None:
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        uans = {i[1] for i in device.identifiers if i[0] == DOMAIN}
        if uans and not uans & keep:
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


# ── Servicii ─────────────────────────────────────────────────
def _all_coordinators(hass: HomeAssistant) -> dict[str, HidroCoordinator]:
    out: dict[str, HidroCoordinator] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            out.update(entry.runtime_data.coordinators)
    return out


def _resolve(hass: HomeAssistant, call: ServiceCall, *, single: bool) -> list[HidroCoordinator]:
    coords = _all_coordinators(hass)
    if not coords:
        raise ServiceValidationError("Integrarea Hidroelectrica nu are conturi încărcate.")
    wanted: set[str] = set()
    if account := call.data.get(ATTR_ACCOUNT):
        wanted.add(str(account).strip())
    device_ids = call.data.get("device_id") or []
    if isinstance(device_ids, str):
        device_ids = [device_ids]
    registry = dr.async_get(hass)
    for dev_id in device_ids:
        device = registry.async_get(dev_id)
        if device:
            wanted |= {i[1] for i in device.identifiers if i[0] == DOMAIN}
    if wanted:
        missing = wanted - set(coords)
        if missing:
            raise ServiceValidationError(f"Cont necunoscut: {', '.join(sorted(missing))}")
        selected = [coords[u] for u in sorted(wanted)]
    else:
        selected = list(coords.values())
    if single and len(selected) != 1:
        raise ServiceValidationError(
            "Alegeți un singur loc de consum (câmpul 'account' sau 'device_id'). "
            f"Disponibile: {', '.join(sorted(coords))}"
        )
    return selected


def _register_services(hass: HomeAssistant) -> None:
    async def submit(call: ServiceCall) -> ServiceResponse:
        coord = _resolve(hass, call, single=True)[0]
        result = await async_submit_reading(
            coord,
            index=call.data.get(ATTR_INDEX),
            readings=call.data.get(ATTR_READINGS),
            validate_only=call.data[ATTR_VALIDATE_ONLY],
        )
        return {"account": coord.uan, **result} if call.return_response else None

    async def refresh(call: ServiceCall) -> None:
        coords = _resolve(hass, call, single=False)
        if call.data[ATTR_FULL]:
            for coord in coords:
                await coord.async_full_refresh()
        else:
            await asyncio.gather(*(c.async_refresh() for c in coords))

    async def get_data(call: ServiceCall) -> ServiceResponse:
        sections = call.data.get(ATTR_SECTIONS) or DATA_SECTIONS
        result: dict[str, Any] = {}
        for coord in _resolve(hass, call, single=False):
            data = coord.data or {}
            result[coord.uan] = {
                "account_number": coord.account_number,
                "address": coord.address,
                "is_prosumer": data.get("is_prosumer"),
                "consumption_index": data.get("consumption_index"),
                **{s: p.jsonable(data.get(s)) for s in sections},
            }
        return {"accounts": result}

    hass.services.async_register(
        DOMAIN, SERVICE_SUBMIT_READING, submit, schema=SUBMIT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, SERVICE_REFRESH, refresh, schema=REFRESH_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_GET_DATA, get_data, schema=GET_DATA_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
