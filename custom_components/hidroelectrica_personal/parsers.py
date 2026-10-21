"""Transformă răspunsurile brute SEW în structuri simple, ușor de folosit în entități.

Fără dependențe de Home Assistant, ca să poată fi testat izolat.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any

from .const import REGISTER_CONSUMPTION, REGISTER_PRODUCTION

MONTHS_RO = {
    1: "ianuarie", 2: "februarie", 3: "martie", 4: "aprilie", 5: "mai", 6: "iunie",
    7: "iulie", 8: "august", 9: "septembrie", 10: "octombrie", 11: "noiembrie", 12: "decembrie",
}
COMPENSATION_PREFIXES = ("comp anre", "comp ", "compensare")

_THOUSANDS_DOT = re.compile(r"^-?\d{1,3}(\.\d{3})+$")


# ── Primitive ─────────────────────────────────────────────────
def to_float(value: Any) -> float | None:
    """Parsează sume/indecși în format românesc sau internațional.

    "1.234,56" → 1234.56 · "1234,56" → 1234.56 · "123.45" → 123.45
    "1.234" → 1234 (separator de mii) · 12 → 12.0 · "" → None
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace(" ", "")
    text = re.sub(r"(?i)(lei|ron|kwh)$", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".") if text.count(",") == 1 else text.replace(",", "")
    elif _THOUSANDS_DOT.match(text):
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def to_date(value: Any) -> date | None:
    """Parsează datele întâlnite în API (dd/MM/yyyy, yyyyMMdd, ISO, MM/dd/yyyy)."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().rstrip("Z")
    if " " in text:
        text = text.split(" ")[0]
    if "T" in text:
        text = text.split("T")[0]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y%m%d", "%d.%m.%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _data(resp: Any) -> Any:
    if isinstance(resp, dict):
        res = resp.get("result")
        if isinstance(res, dict):
            return res.get("Data")
    return None


def _as_list(value: Any, *keys: str) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in keys:
            inner = value.get(key)
            if isinstance(inner, list) and inner:
                return inner
    return []


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


# ── GetBill ───────────────────────────────────────────────────
def parse_bill(resp: Any) -> dict:
    res = resp.get("result") if isinstance(resp, dict) else None
    if not isinstance(res, dict):
        return {}
    invoice = res.get("invoicenumber") or None
    if isinstance(invoice, str) and invoice.endswith("="):  # valoare criptată
        invoice = None
    return {
        "balance": to_float(res.get("rembalance")),
        "bill_amount": to_float(res.get("billamount")),
        "due_date": to_date(res.get("duedate")),
        "invoice_number": invoice,
    }


# ── GetWindowDates ────────────────────────────────────────────
def _day_in_month(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, monthrange(year, month)[1]))


def parse_window(resp_plain: Any, resp_enc: Any, today: date | None = None) -> dict:
    today = today or date.today()
    wd = _data(resp_plain)
    if not isinstance(wd, dict) or not wd:
        wd = _data(resp_enc)
    if not isinstance(wd, dict):
        wd = {}

    plain = _data(resp_plain)
    flag = plain.get("Is_Window_Open") if isinstance(plain, dict) else None
    is_open = str(flag).strip() == "1" if flag is not None else None

    try:
        open_day = int(str(wd.get("OpeningDate", "")).strip())
        close_day = int(str(wd.get("ClosingDate", "")).strip())
    except ValueError:
        open_day = close_day = None  # type: ignore[assignment]

    next_open = to_date(wd.get("NextMonthOpeningDate"))
    next_close = to_date(wd.get("NextMonthClosingDate"))

    start = end = None
    if open_day and close_day:
        # Fereastra curentă / următoare calculată din zilele lunii.
        # (NextMonthClosingDate rămâne uneori pe luna curentă — bug cunoscut.)
        span = close_day - open_day if close_day >= open_day else None
        candidates = []
        for delta_month in (-1, 0, 1):
            y, m = today.year, today.month + delta_month
            if m == 0:
                y, m = y - 1, 12
            elif m == 13:
                y, m = y + 1, 1
            s = _day_in_month(y, m, open_day)
            if span is not None:
                e = _day_in_month(y, m, close_day)
            else:
                ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
                e = _day_in_month(ny, nm, close_day)
            candidates.append((s, e))
        current = next(((s, e) for s, e in candidates if s <= today <= e), None)
        if current:
            start, end = current
            if is_open is None:
                is_open = True
        else:
            upcoming = [(s, e) for s, e in candidates if s > today]
            if upcoming:
                start, end = upcoming[0]
            if next_open and next_open > today:
                # NextMonthOpeningDate e corectă; durata o luăm din zilele ferestrei.
                length = (end - start).days if start and end else 0
                start, end = next_open, next_open + timedelta(days=length)
    elif next_open:
        start, end = next_open, next_close if next_close and next_close >= next_open else None

    return {
        "is_open": bool(is_open),
        "start": start,
        "end": end,
        "opening_day": open_day,
        "closing_day": close_day,
        "raw_next_opening": wd.get("NextMonthOpeningDate"),
        "raw_next_closing": wd.get("NextMonthClosingDate"),
    }


# ── GetPods / GetMultiMeter / GetPreviousMeterRead ───────────
def parse_pod(resp: Any) -> dict:
    lst = _as_list(_data(resp), "objPodData")
    if not lst:
        return {}
    first = lst[0]
    return {
        "pod": str(first.get("pod") or first.get("podValue") or ""),
        "installation": str(first.get("installation") or first.get("InstallationNumber") or ""),
        "account_id": str(first.get("accountID") or ""),
        "contract_account_id": str(first.get("contractAccountID") or ""),
    }


def parse_meter(resp: Any) -> dict:
    res = resp.get("result") if isinstance(resp, dict) else None
    details = res.get("MeterDetails") if isinstance(res, dict) else None
    if isinstance(details, list) and details:
        return dict(details[0])
    return {}


def parse_previous_reads(resp: Any) -> list[dict]:
    data = _data(resp)
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        lst = _as_list(data, "objPreviousMeterReadData")
        if lst:
            return lst
        return [data] if data else []
    return []


# ── Istoric citiri ────────────────────────────────────────────
def parse_counter_series(resp: Any) -> list[dict]:
    return [e for e in _as_list(_data(resp), "objMeterCounterSeriesList") if isinstance(e, dict)]


def active_series(series: list[dict]) -> str | None:
    best, best_date = None, None
    for entry in series:
        sid = entry.get("CounterSeries") or entry.get("MeterCounterSeriesId")
        d = to_date(entry.get("MrDate"))
        if sid and d and (best_date is None or d > best_date):
            best, best_date = str(sid), d
    return best


def series_fallback_index(series: list[dict], active: str | None) -> tuple[float | None, date | None]:
    target = None
    for entry in series:
        sid = entry.get("CounterSeries") or entry.get("MeterCounterSeriesId")
        if active and str(sid) == active:
            target = entry
            break
    target = target or (series[0] if series else None)
    if not target:
        return None, None
    parts = [p for p in str(target.get("Index") or "").split(",") if p.strip()]
    return (to_float(parts[-1]) if parts else None), to_date(target.get("MrDate"))


def parse_reads(resp: Any, active: str | None) -> list[dict]:
    raw = _as_list(_data(resp), "objMeterReadHistoryData", "objMeterReadData", "objHistoryData")
    reads = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        d = to_date(r.get("Date"))
        series = str(r.get("CounterSeries") or r.get("MeterCounterSeriesId") or "")
        reads.append({
            "date": d,
            "index": to_float(r.get("Index")),
            "register": r.get("Registers") or "",
            "register_description": r.get("RegisterDescription") or "",
            "reading_type": r.get("ReadingType") or "",
            "series": series,
            "pod": r.get("POD") or "",
        })
    if active and any(r["series"] == active for r in reads):
        reads = [r for r in reads if r["series"] == active]
    reads.sort(key=lambda r: r["date"] or date.min, reverse=True)
    return reads


def reads_for_register(reads: list[dict], register: str) -> list[dict]:
    if register == REGISTER_CONSUMPTION:
        # Conturile non-prosumator pot avea câmpul Registers gol.
        return [r for r in reads if r["register"] in (REGISTER_CONSUMPTION, "")] or [
            r for r in reads if r["register"] != REGISTER_PRODUCTION
        ]
    return [r for r in reads if r["register"] == register]


def is_prosumer(reads: list[dict]) -> bool:
    return any(r["register"] == REGISTER_PRODUCTION for r in reads)


# ── Consum ────────────────────────────────────────────────────
def parse_usage(resp: Any) -> list[dict]:
    raw = _as_list(_data(resp), "objUsageGenerationResultSetTwo")
    out = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        try:
            year, month = int(e.get("Year") or 0), int(e.get("Month") or 0)
        except (TypeError, ValueError):
            continue
        if not year or not 1 <= month <= 12:
            continue
        try:
            days = int(to_float(e.get("BillingDays")) or 0)
        except (TypeError, ValueError):
            days = 0
        kwh = to_float(e.get("value"))
        out.append({
            "year": year,
            "month": month,
            "kwh": kwh,
            "cost": to_float(e.get("UsageValue")),
            "billing_days": days,
            "daily_kwh": round(kwh / days, 2) if kwh is not None and days else None,
            "from": to_date(e.get("FromDate")),
            "to": to_date(e.get("ToDate")),
        })
    out.sort(key=lambda e: (e["year"], e["month"]))
    return out


# ── Facturi și plăți ──────────────────────────────────────────
def parse_invoices(resp: Any) -> list[dict]:
    res = resp.get("result") if isinstance(resp, dict) else None
    if not isinstance(res, dict):
        return []
    raw = res.get("objBillingHistoryEntity") or _as_list(res.get("Data"), "objBillingHistoryData", "objBillingData")
    out = [{
        "date": to_date(e.get("invoiceDate")),
        "due_date": to_date(e.get("dueDate")),
        "amount": to_float(e.get("amount")),
        "type": e.get("invoiceType") or "",
        "invoice_id": e.get("invoiceId") or "",
    } for e in raw if isinstance(e, dict)]
    out.sort(key=lambda e: e["date"] or date.min, reverse=True)
    return out


def is_compensation(channel: str) -> bool:
    return str(channel or "").strip().lower().startswith(COMPENSATION_PREFIXES)


def parse_payments(resp: Any) -> list[dict]:
    res = resp.get("result") if isinstance(resp, dict) else None
    if not isinstance(res, dict):
        return []
    out = [{
        "date": to_date(e.get("paymentDate")),
        "amount": to_float(e.get("amount")),
        "channel": e.get("channel") or "",
        "compensation": is_compensation(e.get("channel") or ""),
    } for e in res.get("objBillingPaymentHistoryEntity") or [] if isinstance(e, dict)]
    out.sort(key=lambda e: e["date"] or date.min, reverse=True)
    return out


# ── Agregări folosite de senzori ──────────────────────────────
def year_total(entries: list[dict], key: str, year: int, date_key: str = "date") -> float:
    total = 0.0
    for e in entries:
        d = e.get(date_key)
        if d and d.year == year and e.get(key) is not None:
            total += e[key]
    return round(total, 2)


def jsonable(value: Any) -> Any:
    """Convertește recursiv date/datetime în ISO, pentru atribute și răspunsuri de serviciu."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value
