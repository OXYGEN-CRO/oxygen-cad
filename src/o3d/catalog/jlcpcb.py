"""Read-only, ephemeral JLCPCB Components API; live authentication is unverified.

Protocol evidence: mixelpixx/KiCAD-MCP-Server at
cbb59a6203997f5e916be7e0480d63fe5519134a, python/commands/jlcpcb.py.
This is a community implementation, not a substitute for approved JLCAPI docs.
See docs/JLCAPI.md for enrollment, provenance and validation limits.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections.abc import Mapping, Sequence

import httpx

from .http import USER_AGENT

ENDPOINT = "https://open.jlcpcb.com/overseas/openapi/component/getComponentDetailByCode"
DETAIL_PATH = "/overseas/openapi/component/getComponentDetailByCode"
LIBRARY_PATH = "/overseas/openapi/component/getComponentLibraryList"
FEED_PATH = "/overseas/openapi/component/getComponentInfos"
_READ_PATHS = frozenset((DETAIL_PATH, LIBRARY_PATH, FEED_PATH))
DOCS_URL = "https://api.jlcpcb.com/docs/start"
MAX_CODES = 1000
MAX_RESPONSE_BYTES = 10_000_000
_CREDENTIAL_NAMES = ("JLCPCB_APP_ID", "JLCPCB_ACCESS_KEY", "JLCPCB_SECRET_KEY")


class JLCPCBError(RuntimeError):
    """Sanitized failure; provider response bodies and auth headers are omitted."""

    def __init__(self, message: str, *, http_status: int | None = None, api_code: int | None = None):
        super().__init__(message)
        self.http_status, self.api_code = http_status, api_code


class JLCPCBConfigurationError(JLCPCBError):
    """Credentials are absent or cannot be used safely in an auth header."""


class JLCPCBPermissionError(JLCPCBError):
    """The provider's JSON API code explicitly denied permission (403)."""


class JLCPCBClient:
    """Component details and one catalog page: no uploads, quotes, or persistence.

    ``transport`` is a test seam. Success through an injected transport never
    establishes ``real_authenticated_verified``. Credential values are private,
    absent from repr/status/errors, and never logged by this adapter.
    """

    def __init__(self, app_id: str | None = None, access_key: str | None = None,
                 secret_key: str | None = None, *, transport: httpx.BaseTransport | None = None):
        self._credentials = tuple(self._credential(v) for v in (app_id, access_key, secret_key))
        for value in self._credentials[:2]:
            if value and not re.fullmatch(r"[A-Za-z0-9._:-]{1,1024}", value):
                raise JLCPCBConfigurationError("Invalid JLCPCB app ID or access key format")
        self._real_transport = transport is None
        self._real_authenticated_verified = False
        self._last_request: dict | None = None
        self._client = httpx.Client(transport=transport, timeout=30.0, follow_redirects=False,
                                    trust_env=False, headers={"User-Agent": USER_AGENT})

    @staticmethod
    def _credential(value: str | None) -> str | None:
        if value is None or value == "" or (isinstance(value, str) and not value.strip()):
            return None
        if not isinstance(value, str) or len(value) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise JLCPCBConfigurationError("Invalid JLCPCB credential format")
        return value

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *,
                 transport: httpx.BaseTransport | None = None) -> JLCPCBClient:
        """Read explicit environment only; never load or create a .env file."""
        values = os.environ if env is None else env
        return cls(values.get("JLCPCB_APP_ID"),
                   values.get("JLCPCB_ACCESS_KEY") or values.get("JLCPCB_API_KEY"),
                   values.get("JLCPCB_SECRET_KEY") or values.get("JLCPCB_API_SECRET"),
                   transport=transport)

    def configuration_status(self) -> dict:
        missing = [name for name, value in zip(_CREDENTIAL_NAMES, self._credentials) if not value]
        return {"configured": not missing, "missing_credentials": missing,
                "endpoint": ENDPOINT, "docs_url": DOCS_URL,
                "protocol_evidence": "pinned-community-implementation",
                "read_endpoints": {"details": ENDPOINT, "library": "https://open.jlcpcb.com" + LIBRARY_PATH,
                                   "feed": "https://open.jlcpcb.com" + FEED_PATH},
                "last_request": dict(self._last_request) if self._last_request else None,
                "real_authenticated_verified": self._real_authenticated_verified}

    def __repr__(self) -> str:
        return f"JLCPCBClient(configured={self.configuration_status()['configured']})"

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JLCPCBClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _authorization(self, body: bytes, path: str = DETAIL_PATH) -> str:
        status = self.configuration_status()
        if not status["configured"]:
            raise JLCPCBConfigurationError("Missing JLCPCB credentials: " + ", ".join(status["missing_credentials"]))
        app_id, access_key, secret_key = self._credentials
        nonce, timestamp = secrets.token_hex(16), str(int(time.time()))
        canonical = f"POST\n{path}\n{timestamp}\n{nonce}\n".encode() + body + b"\n"
        signature = base64.b64encode(hmac.new(secret_key.encode("utf-8"), canonical, hashlib.sha256).digest()).decode("ascii")
        return (f'JOP appid="{app_id}",accesskey="{access_key}",nonce="{nonce}",'
                f'timestamp="{timestamp}",signature="{signature}"')

    def get_component_details(self, codes: Sequence[str]) -> list[dict]:
        """Return raw details for 0–1000 exact C-codes, without saving offers.

        Unknown codes may be omitted by the provider. An API or shape error is
        never converted to an empty successful response. No automatic retry.
        """
        if not isinstance(codes, (list, tuple)) or len(codes) > MAX_CODES:
            raise ValueError("Expected a list or tuple of at most 1000 JLCPCB C-codes")
        if any(not isinstance(code, str) or not re.fullmatch(r"C[0-9]{1,12}", code) for code in codes):
            raise ValueError("JLCPCB codes must be C followed by 1–12 digits")
        if not codes:
            return []
        data = self._request_data(DETAIL_PATH, {"componentCodes": list(codes)})
        if isinstance(data, dict):
            data = data.get("componentDetailResponseVOList")
        if (not isinstance(data, list) or len(data) > len(codes)
                or any(not isinstance(item, dict) or item.get("componentCode") not in codes for item in data)):
            self._last_request["outcome"] = "invalid_response"
            raise JLCPCBError("JLCPCB returned an unexpected component detail shape")
        self._record_success(len(data))
        return data

    def get_component_library_page(self, current_page: int = 1, page_size: int = 30) -> dict | list:
        """Fetch exactly one library page, retaining the provider's raw shape.

        No automatic traversal or persistence; page size is bounded to 100.
        Endpoint and pagination fields are supported by pinned community code.
        """
        if type(current_page) is not int or not 1 <= current_page <= 1_000_000:
            raise ValueError("current_page must be an integer from 1 to 1000000")
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("page_size must be an integer from 1 to 100")
        return self._catalog_page(LIBRARY_PATH, {"currentPage": current_page, "pageSize": page_size})

    def get_component_feed(self, last_key: str | None = None) -> dict | list:
        """Fetch exactly one provider-sized cursor batch; no bulk traversal.

        The feed contract offers no documented page-size field. Responses remain
        bounded by MAX_RESPONSE_BYTES. Returned cursors are not auto-followed.
        """
        if last_key is not None and (not isinstance(last_key, str) or not 1 <= len(last_key) <= 2048
                                     or any(ord(c) < 32 or ord(c) == 127 for c in last_key)):
            raise ValueError("last_key must be a nonempty cursor of at most 2048 characters without controls")
        return self._catalog_page(FEED_PATH, {} if last_key is None else {"lastKey": last_key})

    def _catalog_page(self, path: str, body: dict) -> dict | list:
        data = self._request_data(path, body)
        if not isinstance(data, (dict, list)):
            self._last_request["outcome"] = "invalid_response"
            raise JLCPCBError("JLCPCB returned an unexpected catalog page shape")
        self._record_success(len(data) if isinstance(data, list) else None)
        return data

    def _record_success(self, returned_items: int | None) -> None:
        self._last_request.update(outcome="success", returned_items=returned_items)
        if self._real_transport:
            self._real_authenticated_verified = True

    def _request_data(self, path: str, payload: dict):
        if path not in _READ_PATHS:
            raise ValueError("Endpoint is outside the read-only Components API allowlist")
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Authorization": self._authorization(body, path), "Content-Type": "application/json",
                   "Accept": "application/json"}
        self._last_request = {"endpoint": "https://open.jlcpcb.com" + path, "http_status": None,
                              "api_code": None, "outcome": "failed", "returned_items": 0,
                              "real_network": self._real_transport}
        try:
            with self._client.stream("POST", "https://open.jlcpcb.com" + path, content=body, headers=headers,
                                     follow_redirects=False) as response:
                self._last_request["http_status"] = response.status_code
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise JLCPCBError("JLCPCB response exceeds the size limit")
                    chunks.append(chunk)
        except httpx.HTTPError:
            # HTTP exceptions can contain a credential-bearing Request object.
            raise JLCPCBError("JLCPCB component lookup transport failed") from None
        try:
            result = json.loads(b"".join(chunks))
        except (ValueError, UnicodeError):
            if not 200 <= response.status_code < 300:
                raise JLCPCBError(f"JLCPCB component lookup returned HTTP {response.status_code}",
                                  http_status=response.status_code) from None
            raise JLCPCBError("JLCPCB returned invalid JSON") from None
        code = result.get("code") if isinstance(result, dict) else None
        code = code if type(code) is int else None
        self._last_request["api_code"] = code
        if code == 403:
            self._last_request["outcome"] = "permission_denied"
            raise JLCPCBPermissionError("JLCPCB Components API permission denied; enable the app's Components API scope",
                                        http_status=response.status_code, api_code=code)
        if not 200 <= response.status_code < 300:
            raise JLCPCBError(f"JLCPCB component lookup returned HTTP {response.status_code}",
                              http_status=response.status_code, api_code=code)
        if code != 200:
            raise JLCPCBError("JLCPCB component lookup returned a non-success API code",
                              http_status=response.status_code, api_code=code)
        return result.get("data")
