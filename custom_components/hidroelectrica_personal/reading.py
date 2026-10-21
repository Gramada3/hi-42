"""Trimiterea autocitirii — folosită de buton și de serviciul submit_reading."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from . import parsers as p
from .api import HidroApiError, HidroRequestError, sew_message
from .coordinator import HidroCoordinator

_LOGGER = logging.getLogger(__name__)

_ENTITY_KEYS = (
    "contractAccountID", "accountID", "equipmentNo", "registerNo", "registerType", "uom",
    "preDecimals", "postDecimals", "noMROrder", "prevMRResult", "prevMRDate", "prevMRRsn",
    "prevMRCat", "serialNumber", "pod", "registerCat", "distributor", "meterInterval",
    "supplier", "distCustomer", "distCustomerId", "distContract", "distContractDate",
)


def _usage_entity(prev: dict, value: int, read_date: str) -> dict:
    # Cheile trebuie să rămână identice cu cele din GetPreviousMeterRead.
    entity = {k: prev.get(k, "") if prev.get(k) is not None else "" for k in _ENTITY_KEYS}
    entity["uom"] = entity["uom"] or "KWH"
    entity["newmeterread"] = str(value)
    entity["NewMeterReadDate"] = read_date
    return entity


def _register_label(prev: dict) -> str:
    return str(prev.get("registerNo") or prev.get("registerType") or "?")


async def async_submit_reading(
    coord: HidroCoordinator,
    index: float | None = None,
    readings: dict[str, float] | None = None,
    validate_only: bool = False,
) -> dict[str, Any]:
    data = coord.data or {}
    pod = data.get("pod") or {}
    if not pod.get("pod"):
        raise HomeAssistantError(f"POD-ul locului de consum {coord.uan} nu este cunoscut încă.")

    try:
        prev_resp = await coord.client.async_get_previous_meter_read(
            coord.uan, pod.get("installation", ""), pod["pod"], pod.get("account_id", "")
        )
    except HidroApiError as err:
        raise HomeAssistantError(f"Nu s-a putut citi indexul anterior: {err}") from err

    previous = p.parse_previous_reads(prev_resp)
    if not previous:
        window = data.get("window") or {}
        when = ""
        if window.get("start"):
            when = f" Următoarea perioadă: {p.iso(window['start'])} – {p.iso(window.get('end'))}."
        raise ServiceValidationError(
            f"Fereastra de autocitire pentru {coord.uan} este închisă.{when}"
        )

    readings = {str(k): v for k, v in (readings or {}).items()}
    if index is None and not readings:
        raise ServiceValidationError("Specificați indexul de trimis.")
    if index is not None and len(previous) > 1 and not readings:
        labels = ", ".join(_register_label(x) for x in previous)
        raise ServiceValidationError(
            f"Contorul are {len(previous)} registre ({labels}); folosiți câmpul 'readings' "
            "pentru a da câte un index fiecăruia."
        )

    read_date = datetime.now().strftime("%d/%m/%Y")
    entities, summary = [], []
    for prev in previous:
        label = _register_label(prev)
        value = readings.get(label, readings.get(str(prev.get("registerType") or "")))
        if value is None:
            value = index
        if value is None:
            raise ServiceValidationError(f"Lipsește indexul pentru registrul {label}.")
        if float(value) != int(value) or value < 0:
            raise ServiceValidationError(f"Indexul trebuie să fie un număr întreg pozitiv (primit {value}).")
        value = int(value)
        last = p.to_float(prev.get("prevMRResult"))
        if last is not None and value < last:
            raise ServiceValidationError(
                f"Indexul {value} este mai mic decât ultimul index înregistrat ({int(last)}) "
                f"pentru registrul {label}."
            )
        entities.append(_usage_entity(prev, value, read_date))
        summary.append({
            "register": label,
            "index": value,
            "previous_index": last,
            "consumption": round(value - last, 2) if last is not None else None,
        })

    installation, account = pod.get("installation", ""), coord.account_number
    try:
        validation = await coord.client.async_validate_meter_value(pod["pod"], installation, account, entities)
        if validate_only:
            return {"submitted": False, "validated": True, "readings": summary,
                    "message": sew_message(validation)}
        result = await coord.client.async_submit_meter_read(pod["pod"], installation, account, entities)
    except HidroRequestError as err:
        msg = sew_message(err.body) or str(err)
        raise HomeAssistantError(f"Hidroelectrica a respins indexul: {msg}") from err
    except HidroApiError as err:
        raise HomeAssistantError(f"Eroare la trimiterea indexului: {err}") from err

    _LOGGER.info("Autocitire trimisă pentru %s: %s", coord.uan, summary)
    coord.hass.async_create_task(coord.async_full_refresh())
    return {"submitted": True, "validated": True, "readings": summary, "message": sew_message(result)}
