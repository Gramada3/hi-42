"""Config flow pentru Hidroelectrica (personal)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import HidroApiClient, HidroApiError, HidroAuthError
from .const import (
    CONF_ACCOUNTS,
    CONF_HISTORY_DAYS,
    CONF_SELECTED_ACCOUNTS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

CREDENTIALS_SCHEMA = vol.Schema({
    vol.Required(CONF_USERNAME): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")),
    vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")),
})


def _account_label(uan: str, info: dict) -> str:
    parts = [x.strip() for x in (info.get("address") or "").split(",") if x.strip()]
    if len(parts) >= 2:
        address = f"{parts[1].title()} {parts[0]}" + (f" ({parts[3].upper()})" if len(parts) > 3 else "")
    else:
        address = info.get("address") or "fără adresă"
    return f"{address} ➜ {uan}"


def _settings_schema(accounts: dict[str, dict], defaults: Mapping[str, Any]) -> vol.Schema:
    options = sorted(
        (SelectOptionDict(value=uan, label=_account_label(uan, info)) for uan, info in accounts.items()),
        key=lambda o: o["label"].lower(),
    )
    selected = [u for u in defaults.get(CONF_SELECTED_ACCOUNTS) or list(accounts) if u in accounts]
    return vol.Schema({
        vol.Required(CONF_SELECTED_ACCOUNTS, default=selected): SelectSelector(
            SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
        ),
        vol.Required(
            CONF_UPDATE_INTERVAL, default=defaults.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        ): NumberSelector(NumberSelectorConfig(
            min=MIN_UPDATE_INTERVAL, max=MAX_UPDATE_INTERVAL, step=60,
            unit_of_measurement="s", mode=NumberSelectorMode.BOX,
        )),
        vol.Required(
            CONF_HISTORY_DAYS, default=defaults.get(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS)
        ): NumberSelector(NumberSelectorConfig(
            min=90, max=3650, step=1, unit_of_measurement="zile", mode=NumberSelectorMode.BOX,
        )),
    })


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_SELECTED_ACCOUNTS: list(user_input[CONF_SELECTED_ACCOUNTS]),
        CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
        CONF_HISTORY_DAYS: int(user_input[CONF_HISTORY_DAYS]),
    }


class HidroConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._accounts: dict[str, dict] = {}

    async def _try_login(self, username: str, password: str) -> tuple[dict[str, dict] | None, str | None]:
        client = HidroApiClient(async_get_clientsession(self.hass), username, password)
        try:
            await client.async_login()
            accounts = await client.async_get_accounts()
        except HidroAuthError:
            return None, "invalid_auth"
        except HidroApiError as err:
            _LOGGER.warning("Conectare iHidro eșuată: %s", err)
            return None, "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Eroare neașteptată la conectare")
            return None, "unknown"
        if not accounts:
            return None, "no_accounts"
        return accounts, None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()
            accounts, error = await self._try_login(username, user_input[CONF_PASSWORD])
            if error:
                errors["base"] = error
            else:
                self._credentials = {CONF_USERNAME: username, CONF_PASSWORD: user_input[CONF_PASSWORD]}
                self._accounts = accounts or {}
                return await self.async_step_accounts()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(CREDENTIALS_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_accounts(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_SELECTED_ACCOUNTS):
                errors["base"] = "no_account_selected"
            else:
                return self.async_create_entry(
                    title=f"Hidroelectrica ({self._credentials[CONF_USERNAME]})",
                    data={**self._credentials, CONF_ACCOUNTS: self._accounts},
                    options=_clean(user_input),
                )
        return self.async_show_form(
            step_id="accounts",
            data_schema=_settings_schema(self._accounts, user_input or {}),
            errors=errors,
            description_placeholders={"count": str(len(self._accounts))},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            accounts, error = await self._try_login(entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD], CONF_ACCOUNTS: accounts},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password"))}),
            errors=errors,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> HidroOptionsFlow:
        return HidroOptionsFlow()


class HidroOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        accounts: dict[str, dict] = dict(entry.data.get(CONF_ACCOUNTS, {}))
        errors: dict[str, str] = {}

        if user_input is None:
            # Reîmprospătăm lista de conturi (poate ai adăugat un loc de consum nou în iHidro).
            runtime = getattr(entry, "runtime_data", None)
            if runtime is not None:
                try:
                    fresh = await runtime.client.async_get_accounts()
                except HidroApiError:
                    fresh = {}
                if fresh and fresh != accounts:
                    accounts = fresh
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_ACCOUNTS: fresh}
                    )
        elif not user_input.get(CONF_SELECTED_ACCOUNTS):
            errors["base"] = "no_account_selected"
        else:
            return self.async_create_entry(data=_clean(user_input))

        return self.async_show_form(
            step_id="init",
            data_schema=_settings_schema(accounts, user_input or entry.options),
            errors=errors,
        )
