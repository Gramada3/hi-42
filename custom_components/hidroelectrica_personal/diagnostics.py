"""Diagnostice (cu datele personale ascunse)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_ACCOUNTS
from .coordinator import HidroConfigEntry
from .parsers import jsonable

TO_REDACT = {
    CONF_PASSWORD, CONF_USERNAME, "address", "Address", "adresa", "pod", "POD", "podValue",
    "UserID", "UserId", "SessionToken", "key", "tokenId", "FirstName", "LastName", "Name",
    "EmailID", "Email", "Mobile", "MobilePhone", "PhoneNumber", "distCustomer", "distCustomerId",
    "accountID", "account_id", "invoicenumber", "invoice_number", "invoiceId", "invoice_id",
    "serialNumber", "MeterNumber", "equipmentNo", "equipment_no",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: HidroConfigEntry) -> dict[str, Any]:
    accounts = {}
    for uan, coord in entry.runtime_data.coordinators.items():
        data = dict(coord.data or {})
        accounts[f"account_{len(accounts) + 1}"] = {
            "last_update_success": coord.last_update_success,
            "data": async_redact_data(jsonable(data), TO_REDACT),
        }
    return {
        "entry": async_redact_data(
            {**entry.data, CONF_ACCOUNTS: list(entry.data.get(CONF_ACCOUNTS, {}).values())}, TO_REDACT
        ),
        "options": dict(entry.options),
        "accounts": accounts,
    }
