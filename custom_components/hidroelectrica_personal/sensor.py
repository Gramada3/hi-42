"""Senzori Hidroelectrica (personal)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import parsers as p
from .coordinator import HidroConfigEntry, HidroCoordinator
from .entity import HidroEntity

CURRENCY = "RON"
HISTORY_LIMIT = 24


def _latest_usage(d: dict) -> dict | None:
    usage = [u for u in d.get("usage") or [] if u.get("kwh") is not None]
    return usage[-1] if usage else None


def _usage_year(d: dict) -> int | None:
    usage = d.get("usage") or []
    return usage[-1]["year"] if usage else None


def _year_usage(d: dict) -> list[dict]:
    year = _usage_year(d)
    return [u for u in d.get("usage") or [] if u["year"] == year]


def _sum(values) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals), 2) if vals else None


def _payments(d: dict, compensation: bool) -> list[dict]:
    return [x for x in d.get("payments") or [] if x["compensation"] == compensation]


def _year_payments(d: dict, compensation: bool) -> list[dict]:
    year = date.today().year
    return [x for x in _payments(d, compensation) if x["date"] and x["date"].year == year]


# ── Atribute ─────────────────────────────────────────────────
def _balance_attrs(d: dict) -> dict:
    bill = d.get("bill") or {}
    bal = bill.get("balance")
    status = "necunoscut" if bal is None else "de plată" if bal > 0 else "credit" if bal < 0 else "achitat"
    return {
        "status": status,
        "ultima_factura": bill.get("bill_amount"),
        "scadenta": p.iso(bill.get("due_date")),
        "numar_factura": bill.get("invoice_number"),
    }


def _invoice_attrs(d: dict) -> dict:
    inv = d.get("invoices") or []
    latest = inv[0] if inv else {}
    return {
        "data_emiterii": p.iso(latest.get("date")),
        "scadenta": p.iso(latest.get("due_date")),
        "tip": latest.get("type"),
        "facturi": p.jsonable(inv[:HISTORY_LIMIT]),
    }


def _index_attrs(d: dict) -> dict:
    r = d.get("consumption_read") or {}
    prev = (d.get("previous_reads") or [{}])[0]
    reads = [x for x in p.reads_for_register(d.get("reads") or [], "1.8.0")]
    return {
        "data_citirii": p.iso(d.get("consumption_index_date")),
        "tip_citire": r.get("reading_type"),
        "registru": r.get("register") or None,
        "serie_contor": d.get("active_series"),
        "index_anterior_autocitire": p.to_float(prev.get("prevMRResult")),
        "data_anterioara_autocitire": p.iso(p.to_date(prev.get("prevMRDate"))),
        "motiv_citire_anterioara": prev.get("prevMRRsn") or None,
        "serie": prev.get("serialNumber") or None,
        "citiri": p.jsonable([{k: x[k] for k in ("date", "index", "reading_type")} for x in reads[:HISTORY_LIMIT]]),
    }


def _production_attrs(d: dict) -> dict:
    r = d.get("production_read") or {}
    reads = p.reads_for_register(d.get("reads") or [], "1.8.0_P")
    return {
        "data_citirii": p.iso(r.get("date")),
        "tip_citire": r.get("reading_type"),
        "citiri": p.jsonable([{k: x[k] for k in ("date", "index", "reading_type")} for x in reads[:HISTORY_LIMIT]]),
    }


def _last_month_attrs(d: dict) -> dict:
    u = _latest_usage(d) or {}
    return {
        "an": u.get("year"),
        "luna": p.MONTHS_RO.get(u.get("month")) if u else None,
        "cost": u.get("cost"),
        "zile_facturate": u.get("billing_days") or None,
        "consum_mediu_zilnic": u.get("daily_kwh"),
        "perioada": f"{p.iso(u.get('from'))} – {p.iso(u.get('to'))}" if u.get("from") else None,
    }


def _year_attrs(d: dict) -> dict:
    rows = _year_usage(d)
    attrs: dict[str, Any] = {"an": _usage_year(d), "cost_total": _sum(u["cost"] for u in rows)}
    for u in rows:
        attrs[p.MONTHS_RO[u["month"]]] = u["kwh"]
    attrs["istoric"] = p.jsonable(d.get("usage") or [])
    return attrs


def _payment_attrs(d: dict, compensation: bool) -> dict:
    lst = _payments(d, compensation)
    latest = lst[0] if lst else {}
    return {"data": p.iso(latest.get("date")), "canal": latest.get("channel")}


def _year_payment_attrs(d: dict, compensation: bool) -> dict:
    lst = _year_payments(d, compensation)
    return {
        "an": date.today().year,
        "numar": len(lst),
        "plati": p.jsonable(lst),
        "istoric": p.jsonable(_payments(d, compensation)[:HISTORY_LIMIT]),
    }


def _contract_attrs(d: dict, coord: HidroCoordinator) -> dict:
    pod, meter = d.get("pod") or {}, d.get("meter") or {}
    prev = (d.get("previous_reads") or [{}])[0]
    attrs = {
        "cod_incasare": coord.uan,
        "numar_cont": coord.account_number,
        "adresa": coord.address or meter.get("Address"),
        "instalatie": pod.get("installation"),
        "cod_client": pod.get("account_id"),
        "contor": meter.get("MeterNumber"),
        "tip_contor": meter.get("MeterType"),
        "contor_inteligent": meter.get("IsAMI"),
        "stare_contor": meter.get("Status"),
        "distribuitor": prev.get("distributor"),
        "furnizor": prev.get("supplier"),
        "client_distributie": prev.get("distCustomer"),
        "contract_distributie": prev.get("distContract"),
        "data_contract_distributie": prev.get("distContractDate"),
        "prosumator": d.get("is_prosumer"),
    }
    return {k: v for k, v in attrs.items() if v not in (None, "")}


def _window_attrs(d: dict) -> dict:
    w = d.get("window") or {}
    return {
        "deschisa": w.get("is_open"),
        "zi_deschidere": w.get("opening_day"),
        "zi_inchidere": w.get("closing_day"),
    }


# ── Descrieri ────────────────────────────────────────────────
@dataclass(frozen=True, kw_only=True)
class HidroSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], Any]
    attrs_fn: Callable[[dict, HidroCoordinator], dict] | None = None
    prosumer_only: bool = False


SENSORS: tuple[HidroSensorDescription, ...] = (
    HidroSensorDescription(
        key="balance",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        icon="mdi:cash-clock",
        value_fn=lambda d: (d.get("bill") or {}).get("balance"),
        attrs_fn=lambda d, c: _balance_attrs(d),
    ),
    HidroSensorDescription(
        key="due_date",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-alert",
        value_fn=lambda d: (d.get("bill") or {}).get("due_date"),
    ),
    HidroSensorDescription(
        key="last_invoice",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        icon="mdi:file-document-outline",
        value_fn=lambda d: (d.get("bill") or {}).get("bill_amount")
        if (d.get("bill") or {}).get("bill_amount") is not None
        else ((d.get("invoices") or [{}])[0].get("amount")),
        attrs_fn=lambda d, c: _invoice_attrs(d),
    ),
    HidroSensorDescription(
        key="consumption_index",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=0,
        icon="mdi:counter",
        value_fn=lambda d: d.get("consumption_index"),
        attrs_fn=lambda d, c: _index_attrs(d),
    ),
    HidroSensorDescription(
        key="last_reading_date",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-check",
        value_fn=lambda d: d.get("consumption_index_date"),
    ),
    HidroSensorDescription(
        key="production_index",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=0,
        icon="mdi:solar-power-variant",
        value_fn=lambda d: (d.get("production_read") or {}).get("index"),
        attrs_fn=lambda d, c: _production_attrs(d),
        prosumer_only=True,
    ),
    HidroSensorDescription(
        key="window_start",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-start",
        value_fn=lambda d: (d.get("window") or {}).get("start"),
        attrs_fn=lambda d, c: _window_attrs(d),
    ),
    HidroSensorDescription(
        key="window_end",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-end",
        value_fn=lambda d: (d.get("window") or {}).get("end"),
    ),
    HidroSensorDescription(
        key="last_month_consumption",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=0,
        icon="mdi:lightning-bolt",
        value_fn=lambda d: (_latest_usage(d) or {}).get("kwh"),
        attrs_fn=lambda d, c: _last_month_attrs(d),
    ),
    HidroSensorDescription(
        key="last_month_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        icon="mdi:cash",
        value_fn=lambda d: (_latest_usage(d) or {}).get("cost"),
    ),
    HidroSensorDescription(
        key="year_consumption",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=0,
        icon="mdi:chart-bar",
        value_fn=lambda d: _sum(u["kwh"] for u in _year_usage(d)),
        attrs_fn=lambda d, c: _year_attrs(d),
    ),
    HidroSensorDescription(
        key="last_payment",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        icon="mdi:cash-check",
        value_fn=lambda d: (_payments(d, False) or [{}])[0].get("amount"),
        attrs_fn=lambda d, c: _payment_attrs(d, False),
    ),
    HidroSensorDescription(
        key="year_payments",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        icon="mdi:cash-multiple",
        value_fn=lambda d: _sum(x["amount"] for x in _year_payments(d, False)) or 0.0,
        attrs_fn=lambda d, c: _year_payment_attrs(d, False),
    ),
    HidroSensorDescription(
        key="year_compensations",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        icon="mdi:solar-power",
        value_fn=lambda d: _sum(x["amount"] for x in _year_payments(d, True)) or 0.0,
        attrs_fn=lambda d, c: _year_payment_attrs(d, True),
        prosumer_only=True,
    ),
    HidroSensorDescription(
        key="contract",
        icon="mdi:file-sign",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("pod") or {}).get("pod") or None,
        attrs_fn=_contract_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HidroConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    entities = []
    for coord in entry.runtime_data.coordinators.values():
        prosumer = bool((coord.data or {}).get("is_prosumer"))
        entities.extend(
            HidroSensor(coord, desc) for desc in SENSORS if prosumer or not desc.prosumer_only
        )
    async_add_entities(entities)


class HidroSensor(HidroEntity, SensorEntity):
    entity_description: HidroSensorDescription
    # Listele de istoric rămân vizibile în atribute, dar nu se scriu în recorder.
    _unrecorded_attributes = frozenset({"istoric", "facturi", "citiri", "plati"})

    def __init__(self, coordinator: HidroCoordinator, description: HidroSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.data, self.coordinator) if fn and self.data else None
