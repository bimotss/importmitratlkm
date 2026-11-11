import json
import logging
import random
import time
from urllib.parse import quote

import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import credentials, service_account

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
LOG = logging.getLogger(__name__)


class GoogleAPIError(RuntimeError):
    pass


class GoogleSheets:
    def __init__(self, credentials_file, requests_per_minute=45):
        info = json.loads(credentials_file.read_text())
        if info.get("type") == "service_account":
            auth = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        elif info.get("type") == "authorized_user":
            auth = credentials.Credentials.from_authorized_user_info(info, scopes=SCOPES)
        else:
            raise ValueError("Gunakan token OAuth hasil perintah auth atau JSON service account.")
        self.session = AuthorizedSession(auth)
        self.interval = 60 / requests_per_minute
        self.last = {"read": 0.0, "write": 0.0}

    def _pace(self, kind):
        delay = self.last[kind] + self.interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last[kind] = time.monotonic()

    def request(self, method, spreadsheet_id, suffix="", *, params=None, body=None, idempotent=True):
        kind = "read" if method == "GET" else "write"
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{quote(spreadsheet_id, safe='')}{suffix}"
        for attempt in range(6):
            self._pace(kind)
            try:
                response = self.session.request(method, url, params=params, json=body, timeout=(15, 180))
            except (requests.Timeout, requests.ConnectionError) as exc:
                if not idempotent or attempt == 5:
                    raise GoogleAPIError("Koneksi Google terputus; periksa status sebelum mencoba lagi.") from exc
                time.sleep(min(60, 2**attempt) + random.random())
                continue
            if response.ok:
                return response.json()
            try:
                error = response.json().get("error", {})
            except ValueError:
                error = {}
            reasons = {e.get("reason") for e in error.get("errors", [])}
            quota = response.status_code == 429 or bool(reasons & {"rateLimitExceeded", "userRateLimitExceeded"})
            transient = response.status_code in {500, 502, 503, 504}
            if attempt < 5 and (quota or (transient and idempotent)):
                delay = max(60 if quota else 1, min(60, 2**attempt)) + random.random()
                LOG.warning("Google API %s; mencoba lagi dalam %.0f detik", response.status_code, delay)
                time.sleep(delay)
                continue
            message = str(error.get("message", "Google API gagal"))[:500]
            raise GoogleAPIError(f"Google Sheets {response.status_code}: {message}")
        raise GoogleAPIError("Google API tidak merespons.")

    def metadata(self, spreadsheet_id):
        return self.request("GET", spreadsheet_id, params={
            "fields": "spreadsheetId,sheets.properties(sheetId,title,gridProperties)"
        })

    def read(self, spreadsheet_id, a1):
        return self.request("GET", spreadsheet_id, "/values/" + quote(a1, safe=""), params={
            "valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "SERIAL_NUMBER"
        }).get("values", [])

    def read_many(self, spreadsheet_id, ranges):
        return self.request("GET", spreadsheet_id, "/values:batchGet", params={
            "ranges": ranges, "valueRenderOption": "UNFORMATTED_VALUE"
        }).get("valueRanges", [])

    def cells(self, spreadsheet_id, a1, *, effective=False):
        fields = "userEnteredValue,effectiveValue" if effective else "userEnteredValue"
        result = self.request("GET", spreadsheet_id, params={
            "ranges": a1, "includeGridData": "true",
            "fields": f"sheets(data(startRow,startColumn,rowData(values({fields}))))"
        })
        return result["sheets"][0].get("data", [{}])[0].get("rowData", [])

    def write(self, spreadsheet_id, a1, values):
        # RAW preserves strings beginning with '=' as text, including source notes.
        return self.request("PUT", spreadsheet_id, "/values/" + quote(a1, safe=""),
                            params={"valueInputOption": "RAW"}, body={"values": values})

    def batch(self, spreadsheet_id, requests_, *, idempotent=True):
        return self.request("POST", spreadsheet_id, ":batchUpdate", body={"requests": requests_},
                            idempotent=idempotent)

