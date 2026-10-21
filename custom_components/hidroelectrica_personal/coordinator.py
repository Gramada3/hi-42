"""Coordinator per cont (UAN) pentru Hidroelectrica (personal)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import parsers as p
from .api import HidroApiClient, HidroApiError, HidroAuthError
from .const import DOMAIN, HEAVY_REFRESH_EVERY, REGISTER_CONSUMPTION, REGISTER_PRODUCTION

_LOGGER = logging.getLogger(__name__)


@dataclass
class HidroRuntimeData:
    client: HidroApiClient
    coordinators: dict[str, "HidroCoordinator"] = field(default_factory=dict)


type HidroConfigEntry = ConfigEntry[HidroRuntimeData]


class HidroCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Date pentru un singur loc de consum (UAN)."""

    config_entry: HidroConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: HidroConfigEntry,
        client: HidroApiClient,
        uan: str,
        account: dict,
        update_interval: int,
        history_days: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{uan}",
            update_interval=timedelta(seconds=update_interval),
        )
        self.client = client
        self.uan = uan
        self.account_number: str = account.get("account_number", "")
        self.address: str = account.get("address", "")
        self.history_days = history_days
        self._counter = 0
        self._force_heavy = False

    async def async_full_refresh(self) -> None:
        """Refresh complet, inclusiv istoricele."""
        self._force_heavy = True
        await self.async_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        heavy = self._force_heavy or self._counter % HEAVY_REFRESH_EVERY == 0 or not self.data
        self._force_heavy = False
        c, uan = self.client, self.uan
        try:
            if not self.account_number:
                accounts = await c.async_get_accounts()
                self.account_number = accounts.get(uan, {}).get("account_number", "")
                if not self.account_number:
                    raise UpdateFailed(f"Nu s-a găsit AccountNumber pentru {uan}")
            acc = self.account_number

            meter_r, bill_r, wd_r, wd_enc_r, pods_r = await asyncio.gather(
                c.async_get_multi_meter(uan, acc),
                c.async_get_bill(uan, acc),
                c.async_get_window_dates(uan, acc),
                c.async_get_window_dates_enc(uan, acc),
                c.async_get_pods(uan, acc),
            )
            pod = p.parse_pod(pods_r)
            prev_r = None
            if pod.get("pod"):
                prev_r = await c.async_get_previous_meter_read(
                    uan, pod.get("installation", ""), pod["pod"], pod.get("account_id", "")
                )

            raw: dict[str, Any] = {
                "multi_meter": meter_r,
                "bill": bill_r,
                "window_dates": wd_r,
                "window_dates_enc": wd_enc_r,
                "pods": pods_r,
                "previous_meter_read": prev_r,
            }

            prev_raw = (self.data or {}).get("raw", {})
            if heavy:
                today = date.today()
                start = today - timedelta(days=self.history_days)
                inst, pod_v = pod.get("installation", ""), pod.get("pod", "")
                usage_r, billing_r, series_r = await asyncio.gather(
                    c.async_get_usage(uan, acc),
                    c.async_get_billing_history(uan, acc, start.isoformat(), today.isoformat()),
                    c.async_get_meter_counter_series(uan, inst, pod_v) if pod_v else _none(),
                )
                serials = sorted({
                    str(s.get("SerialNumber")) for s in p.parse_counter_series(series_r)
                    if s.get("SerialNumber")
                })
                # Varianta fără SerialNumber e cea verificată în practică;
                # cu seriile explicite încercăm doar dacă prima eșuează.
                history_r = (
                    await c.async_get_meter_read_history(uan, inst, pod_v, []) if pod_v else None
                )
                if history_r is None and serials and pod_v:
                    history_r = await c.async_get_meter_read_history(uan, inst, pod_v, serials)
                raw.update(
                    usage=usage_r,
                    billing_history=billing_r,
                    meter_counter_series=series_r,
                    meter_read_history=history_r,
                )
            else:
                for key in ("usage", "billing_history", "meter_counter_series", "meter_read_history"):
                    raw[key] = prev_raw.get(key)

        except HidroAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except HidroApiError as err:
            raise UpdateFailed(str(err)) from err

        if not any((meter_r, bill_r, wd_r, pods_r)):
            if self.data:
                raise UpdateFailed(f"Niciun endpoint nu a returnat date pentru {uan}")
            raise UpdateFailed(f"Nu s-au putut obține datele pentru {uan} (verificați contul)")

        self._counter += 1
        return self._build(raw)

    def _build(self, raw: dict[str, Any]) -> dict[str, Any]:
        series = p.parse_counter_series(raw.get("meter_counter_series"))
        active = p.active_series(series)
        reads = p.parse_reads(raw.get("meter_read_history"), active)
        prosumer = p.is_prosumer(reads)
        previous = p.parse_previous_reads(raw.get("previous_meter_read"))

        def latest(register: str) -> dict | None:
            lst = [r for r in p.reads_for_register(reads, register) if r["index"] is not None]
            return lst[0] if lst else None

        consumption = latest(REGISTER_CONSUMPTION)
        consumption_index = consumption["index"] if consumption else None
        consumption_date = consumption["date"] if consumption else None
        if consumption_index is None:
            for prev in previous:
                if prev.get("registerType") in (None, "", REGISTER_CONSUMPTION) or len(previous) == 1:
                    consumption_index = p.to_float(prev.get("prevMRResult"))
                    consumption_date = p.to_date(prev.get("prevMRDate"))
                    if consumption_index is not None:
                        break
        if consumption_index is None:
            consumption_index, consumption_date = p.series_fallback_index(series, active)

        return {
            "raw": raw,
            "bill": p.parse_bill(raw.get("bill")),
            "window": p.parse_window(raw.get("window_dates"), raw.get("window_dates_enc")),
            "pod": p.parse_pod(raw.get("pods")),
            "meter": p.parse_meter(raw.get("multi_meter")),
            "previous_reads": previous,
            "counter_series": series,
            "active_series": active,
            "reads": reads,
            "is_prosumer": prosumer,
            "consumption_index": consumption_index,
            "consumption_index_date": consumption_date,
            "consumption_read": consumption,
            "production_read": latest(REGISTER_PRODUCTION) if prosumer else None,
            "usage": p.parse_usage(raw.get("usage")),
            "invoices": p.parse_invoices(raw.get("billing_history")),
            "payments": p.parse_payments(raw.get("billing_history")),
        }


async def _none() -> None:
    return None
