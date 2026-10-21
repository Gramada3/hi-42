"""Client async pentru API-ul Hidroelectrica (platforma SEW, proxy ihidro.ro).

Autentificare în doi pași:
  1. GetId              → key + tokenId
  2. ValidateUserLogin  → UserID + SessionToken   (Basic key:tokenId)
Apelurile ulterioare folosesc Basic UserID:SessionToken cu SourceType=1.
La HTTP 401 se reface login-ul o singură dată și se repetă cererea.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime
from typing import Any

import aiohttp

from .const import (
    API_BASE,
    API_TIMEOUT,
    EP_BILL,
    EP_BILLING_HISTORY,
    EP_GET_ID,
    EP_MASTER_DATA_STATUS,
    EP_METER_COUNTER_SERIES,
    EP_METER_READ_HISTORY,
    EP_METER_VALUE,
    EP_MULTI_METER,
    EP_PODS,
    EP_PREVIOUS_METER_READ,
    EP_SUBMIT_METER_READ,
    EP_USAGE,
    EP_USER_SETTING,
    EP_VALIDATE_LOGIN,
    EP_WINDOW_DATES,
    EP_WINDOW_DATES_ENC,
    LANGUAGE,
    POST_AUTH_HEADERS,
    PRE_AUTH_HEADERS,
)

_LOGGER = logging.getLogger(__name__)


class HidroApiError(Exception):
    """Eroare generică API."""


class HidroAuthError(HidroApiError):
    """Credențiale invalide sau cont blocat."""


class HidroRequestError(HidroApiError):
    """Serverul a răspuns cu eroare la o cerere autentificată."""

    def __init__(self, message: str, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class HidroApiClient:
    """Client pentru un utilizator iHidro (poate avea mai multe conturi/UAN)."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._user_id: str | None = None
        self._session_token: str | None = None
        self._generation = 0
        self._auth_lock = asyncio.Lock()
        self._timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        # Certificatul serverului a avut probleme în trecut. Încercăm întâi
        # cu verificare; dacă eșuează, trecem pe conexiune neverificată.
        self._verify_ssl = True

    # ── Proprietăți ──────────────────────────────────────────
    @property
    def user_id(self) -> str | None:
        return self._user_id

    @property
    def username(self) -> str:
        return self._username

    # ── Transport ────────────────────────────────────────────
    async def _raw_post(self, endpoint: str, payload: dict, headers: dict) -> tuple[int, Any]:
        url = f"{API_BASE}{endpoint}"
        for attempt in (1, 2):
            try:
                async with self._session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self._timeout,
                    ssl=True if self._verify_ssl else False,
                ) as resp:
                    text = await resp.text()
                    try:
                        body: Any = await resp.json(content_type=None) if text else None
                    except ValueError:
                        body = text
                    return resp.status, body
            except (aiohttp.ClientConnectorCertificateError, aiohttp.ClientSSLError) as err:
                if self._verify_ssl and attempt == 1:
                    _LOGGER.warning(
                        "Certificatul TLS al serverului iHidro nu a putut fi verificat (%s); "
                        "se continuă fără verificare TLS.",
                        err,
                    )
                    self._verify_ssl = False
                    continue
                raise HidroApiError(f"{endpoint}: eroare TLS: {err}") from err
            except asyncio.TimeoutError as err:
                raise HidroApiError(f"{endpoint}: timeout") from err
            except aiohttp.ClientError as err:
                raise HidroApiError(f"{endpoint}: {err}") from err
        raise HidroApiError(f"{endpoint}: cerere eșuată")

    def _auth_headers(self) -> dict[str, str]:
        token = base64.b64encode(f"{self._user_id}:{self._session_token}".encode()).decode()
        return {**POST_AUTH_HEADERS, "Authorization": f"Basic {token}"}

    @staticmethod
    def _data(body: Any, label: str) -> Any:
        try:
            return body["result"]["Data"]
        except (KeyError, TypeError) as err:
            raise HidroApiError(f"{label}: răspuns fără result.Data") from err

    # ── Autentificare ────────────────────────────────────────
    async def async_login(self) -> None:
        """Login complet. Aruncă HidroAuthError la credențiale greșite."""
        status, body = await self._raw_post(EP_GET_ID, {}, dict(PRE_AUTH_HEADERS))
        if status != 200:
            raise HidroApiError(f"GetId: HTTP {status}")
        data = self._data(body, "GetId") or {}
        key, token_id = data.get("key"), data.get("tokenId")
        if not key or not token_id:
            raise HidroApiError("GetId nu a returnat key/tokenId")

        pre = base64.b64encode(f"{key}:{token_id}".encode()).decode()
        now = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
        payload = {
            "deviceType": "MobileApp",
            "OperatingSystem": "Android",
            "UpdatedDate": now,
            "Deviceid": "",
            "SessionCode": "",
            "LanguageCode": LANGUAGE,
            "password": self._password,
            "UserId": self._username,
            "TFADeviceid": "",
            "OSVersion": 14,
            "TimeOffSet": "120",
            "LUpdHideShow": now,
            "Browser": "NA",
        }
        status, body = await self._raw_post(
            EP_VALIDATE_LOGIN, payload, {**PRE_AUTH_HEADERS, "Authorization": f"Basic {pre}"}
        )
        if status in (400, 401, 403):
            raise HidroAuthError(f"ValidateUserLogin: HTTP {status}")
        if status != 200:
            raise HidroApiError(f"ValidateUserLogin: HTTP {status}")
        try:
            table = (self._data(body, "ValidateUserLogin") or {}).get("Table") or []
        except HidroApiError as err:
            raise HidroAuthError("Autentificare eșuată") from err
        if not table:
            raise HidroAuthError("Autentificare eșuată (credențiale invalide sau cont blocat)")
        row = table[0]
        user_id, session_token = row.get("UserID"), row.get("SessionToken")
        if not user_id or not session_token:
            raise HidroAuthError("Autentificare eșuată (UserID/SessionToken lipsă)")
        self._user_id = str(user_id)
        self._session_token = str(session_token)
        self._generation += 1
        _LOGGER.debug("Login iHidro reușit (UserID=%s)", self._user_id)

    async def async_ensure_login(self) -> None:
        if self._session_token:
            return
        async with self._auth_lock:
            if not self._session_token:
                await self.async_login()

    def invalidate(self) -> None:
        self._session_token = None

    async def _post(self, endpoint: str, payload: dict, label: str, *, strict: bool = False) -> dict | None:
        """Cerere autentificată, cu re-login la 401.

        strict=False → la eroare HTTP returnează None (date opționale).
        strict=True  → aruncă HidroRequestError cu corpul răspunsului.
        """
        await self.async_ensure_login()
        gen = self._generation
        status, body = await self._raw_post(endpoint, payload, self._auth_headers())
        if status == 401:
            async with self._auth_lock:
                if gen == self._generation:
                    _LOGGER.debug("%s: HTTP 401, se reface login-ul", label)
                    self._session_token = None
                    await self.async_login()
            status, body = await self._raw_post(endpoint, payload, self._auth_headers())
        if status == 401:
            raise HidroAuthError(f"{label}: sesiune respinsă după re-login")
        if status != 200:
            if strict:
                raise HidroRequestError(f"{label}: HTTP {status}", status, body)
            _LOGGER.debug("%s: HTTP %s — %s", label, status, str(body)[:300])
            return None
        if not isinstance(body, dict):
            if strict:
                raise HidroRequestError(f"{label}: răspuns neașteptat", status, body)
            return None
        return body

    def _account_payload(self, uan: str, account_number: str) -> dict:
        return {
            "MeterType": "E",
            "UserID": self._user_id,
            "UtilityAccountNumber": uan,
            "AccountNumber": account_number,
        }

    # ── Cont / utilizator ────────────────────────────────────
    async def async_get_user_setting(self) -> dict | None:
        await self.async_ensure_login()
        return await self._post(EP_USER_SETTING, {"UserID": self._user_id}, "GetUserSetting")

    async def async_get_accounts(self) -> dict[str, dict]:
        """Conturile (UAN) asociate utilizatorului."""
        body = await self.async_get_user_setting() or {}
        data = (body.get("result") or {}).get("Data") or {}
        accounts: dict[str, dict] = {}
        for table in ("Table1", "Table2"):
            for row in data.get(table) or []:
                uan = str(row.get("UtilityAccountNumber") or "").strip()
                if not uan or uan in accounts:
                    continue
                accounts[uan] = {
                    "account_number": str(row.get("AccountNumber") or "").strip(),
                    "address": str(row.get("Address") or "").strip(),
                    "pod": str(row.get("Pod") or "").strip(),
                    "equipment_no": str(row.get("EquipmentNo") or "").strip(),
                    "is_default": bool(row.get("IsDefaultAccount")),
                }
        return accounts

    async def async_get_master_data_status(self) -> dict | None:
        await self.async_ensure_login()
        return await self._post(EP_MASTER_DATA_STATUS, {"UserID": self._user_id}, "GetMasterDataStatus")

    # ── Date per cont ────────────────────────────────────────
    async def async_get_multi_meter(self, uan: str, acc: str) -> dict | None:
        await self.async_ensure_login()
        return await self._post(EP_MULTI_METER, self._account_payload(uan, acc), f"GetMultiMeter {uan}")

    async def async_get_bill(self, uan: str, acc: str) -> dict | None:
        await self.async_ensure_login()
        payload = {
            "LanguageCode": LANGUAGE,
            "UserID": self._user_id,
            "IsBillPDF": "0",
            "UtilityAccountNumber": uan,
            "AccountNumber": acc,
        }
        return await self._post(EP_BILL, payload, f"GetBill {uan}")

    async def async_get_window_dates(self, uan: str, acc: str) -> dict | None:
        await self.async_ensure_login()
        return await self._post(EP_WINDOW_DATES, self._account_payload(uan, acc), f"GetWindowDates {uan}")

    async def async_get_window_dates_enc(self, uan: str, acc: str) -> dict | None:
        await self.async_ensure_login()
        return await self._post(EP_WINDOW_DATES_ENC, self._account_payload(uan, acc), f"GetWindowDatesENC {uan}")

    async def async_get_pods(self, uan: str, acc: str) -> dict | None:
        await self.async_ensure_login()
        return await self._post(EP_PODS, self._account_payload(uan, acc), f"GetPods {uan}")

    async def async_get_previous_meter_read(
        self, uan: str, installation: str, pod: str, customer_number: str
    ) -> dict | None:
        """Ultima citire + metadatele registrelor. HTTP 400 când fereastra e închisă."""
        await self.async_ensure_login()
        payload = {
            "UtilityAccountNumber": uan,
            "InstallationNumber": installation,
            "podValue": pod,
            "LanguageCode": LANGUAGE,
            "UserID": self._user_id,
            "BasicValue": "",
            "CustomerNumber": customer_number,
            "Distributor": "",
        }
        return await self._post(EP_PREVIOUS_METER_READ, payload, f"GetPreviousMeterRead {uan}")

    async def async_get_usage(self, uan: str, acc: str) -> dict | None:
        await self.async_ensure_login()
        payload = {
            "date": "",
            "IsCSR": False,
            "IsUSD": False,
            "Mode": "M",
            "HourlyType": "H",
            "UsageType": "e",
            "UsageOrGeneration": False,
            "GroupId": 0,
            "LanguageCode": LANGUAGE,
            "Type": "D",
            "MeterNumber": "",
            "IsEnterpriseUser": False,
            "SeasonType": 0,
            "DateFromDaily": "",
            "IsNetUsage": False,
            "TimeOffset": "120",
            "UserType": "Residential",
            "DateToDaily": "",
            "UtilityId": 0,
            "IsLastTendays": False,
            "UserID": self._user_id,
            "UtilityAccountNumber": uan,
            "AccountNumber": acc,
        }
        return await self._post(EP_USAGE, payload, f"GetUsageGeneration {uan}")

    async def async_get_billing_history(self, uan: str, acc: str, from_date: str, to_date: str) -> dict | None:
        await self.async_ensure_login()
        payload = {
            "LanguageCode": LANGUAGE,
            "UserID": self._user_id,
            "UtilityAccountNumber": uan,
            "AccountNumber": acc,
            "FromDate": from_date,
            "ToDate": to_date,
        }
        return await self._post(EP_BILLING_HISTORY, payload, f"GetBillingHistoryList {uan}")

    async def async_get_meter_counter_series(self, uan: str, installation: str, pod: str) -> dict | None:
        await self.async_ensure_login()
        payload = {
            "utilityAccountNumber": uan,
            "InstallationNumber": installation,
            "podValue": pod,
            "LanguageCode": LANGUAGE,
        }
        return await self._post(EP_METER_COUNTER_SERIES, payload, f"GetMeterCounterSeries {uan}")

    async def async_get_meter_read_history(
        self, uan: str, installation: str, pod: str, serial_numbers: list[str] | None = None
    ) -> dict | None:
        await self.async_ensure_login()
        payload = {
            "utilityAccountNumber": uan,
            "podValue": pod,
            "LanguageCode": LANGUAGE,
            "InstallationNumber": installation,
            "SerialNumber": serial_numbers or [],
        }
        return await self._post(EP_METER_READ_HISTORY, payload, f"GetMeterReadHistory {uan}")

    # ── Autocitire ───────────────────────────────────────────
    def _reading_payload(self, pod: str, installation: str, acc: str, entities: list[dict]) -> dict:
        return {
            "UserId": self._user_id,
            "podValue": pod,
            "InstallationNumber": installation,
            "AccountNumber": acc,
            "UsageSelfMeterReadEntity": entities,
        }

    async def async_validate_meter_value(
        self, pod: str, installation: str, acc: str, entities: list[dict]
    ) -> dict:
        await self.async_ensure_login()
        body = await self._post(
            EP_METER_VALUE, self._reading_payload(pod, installation, acc, entities),
            f"GetMeterValue {acc}", strict=True,
        )
        _check_sew_status(body, "Validarea indexului")
        return body  # type: ignore[return-value]

    async def async_submit_meter_read(
        self, pod: str, installation: str, acc: str, entities: list[dict]
    ) -> dict:
        await self.async_ensure_login()
        body = await self._post(
            EP_SUBMIT_METER_READ, self._reading_payload(pod, installation, acc, entities),
            f"SubmitSelfMeterRead {acc}", strict=True,
        )
        _check_sew_status(body, "Trimiterea indexului")
        return body  # type: ignore[return-value]


def sew_message(body: Any) -> str | None:
    """Extrage un mesaj lizibil dintr-un răspuns SEW, dacă există."""
    if not isinstance(body, dict):
        return str(body)[:200] if body else None
    for path in (("message",), ("Message",), ("result", "Message"), ("result", "message"),
                 ("result", "Data", "Message"), ("result", "Data", "message")):
        cur: Any = body
        for key in path:
            cur = cur.get(key) if isinstance(cur, dict) else None
        if isinstance(cur, str) and cur.strip():
            return cur.strip()
    return None


def _check_sew_status(body: Any, label: str) -> None:
    if not isinstance(body, dict):
        return
    code = body.get("status_code")
    if code is not None and str(code) != "200":
        raise HidroRequestError(f"{label}: {sew_message(body) or f'cod {code}'}", None, body)
