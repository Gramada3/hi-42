"""Constante pentru integrarea Hidroelectrica (personal)."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "hidroelectrica_personal"
MANUFACTURER = "Hidroelectrica"
ATTRIBUTION = "Date furnizate de Hidroelectrica (platforma iHidro / SEW)"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.BUTTON,
]

# ── Configurare ───────────────────────────────────────────────
CONF_ACCOUNTS = "accounts"  # {uan: {"account_number": str, "address": str, "pod": str}}
CONF_SELECTED_ACCOUNTS = "selected_accounts"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_HISTORY_DAYS = "history_days"

DEFAULT_UPDATE_INTERVAL = 3600  # secunde
MIN_UPDATE_INTERVAL = 300
MAX_UPDATE_INTERVAL = 86400
DEFAULT_HISTORY_DAYS = 730
HEAVY_REFRESH_EVERY = 4  # istoricele se reîncarcă la fiecare al N-lea refresh

# ── API ───────────────────────────────────────────────────────
API_BASE = "https://ihidro.ro"
API_TIMEOUT = 20
LANGUAGE = "RO"

_COMMON_HEADERS = {
    "Content-Type": "application/json",
    "Host": "hidroelectrica-svc.smartcmobile.com",
    "User-Agent": "okhttp/4.9.0",
}
PRE_AUTH_HEADERS = {"SourceType": "0", **_COMMON_HEADERS}
POST_AUTH_HEADERS = {"SourceType": "1", **_COMMON_HEADERS}

EP_GET_ID = "/API/UserLogin/GetId"
EP_VALIDATE_LOGIN = "/API/UserLogin/ValidateUserLogin"
EP_USER_SETTING = "/API/UserLogin/GetUserSetting"
EP_MASTER_DATA_STATUS = "/API/UserLogin/GetMasterDataStatus"
EP_MULTI_METER = "/Service/Usage/GetMultiMeter"
EP_USAGE = "/Service/Usage/GetUsageGeneration"
EP_WINDOW_DATES_ENC = "/Service/SelfMeterReading/GetWindowDatesENC"
EP_WINDOW_DATES = "/Service/SelfMeterReading/GetWindowDates"
EP_PODS = "/Service/SelfMeterReading/GetPods"
EP_METER_VALUE = "/Service/SelfMeterReading/GetMeterValue"
EP_PREVIOUS_METER_READ = "/Service/SelfMeterReading/GetPreviousMeterRead"
EP_SUBMIT_METER_READ = "/Service/SelfMeterReading/SubmitSelfMeterRead"
EP_BILL = "/Service/Billing/GetBill"
EP_BILLING_HISTORY = "/Service/Billing/GetBillingHistoryList"
EP_METER_COUNTER_SERIES = "/Service/IndexHistory/GetMeterCounterSeries"
EP_METER_READ_HISTORY = "/Service/IndexHistory/GetMeterReadHistory"

# ── Registre contor ───────────────────────────────────────────
REGISTER_CONSUMPTION = "1.8.0"
REGISTER_PRODUCTION = "1.8.0_P"

# ── Servicii ──────────────────────────────────────────────────
SERVICE_SUBMIT_READING = "submit_reading"
SERVICE_REFRESH = "refresh"
SERVICE_GET_DATA = "get_data"

ATTR_ACCOUNT = "account"
ATTR_INDEX = "index"
ATTR_READINGS = "readings"
ATTR_VALIDATE_ONLY = "validate_only"
ATTR_FULL = "full"
ATTR_SECTIONS = "sections"
