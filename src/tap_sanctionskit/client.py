"""Bounded, account-scoped HTTPS reads with sanitized failures."""
from __future__ import annotations

import json
import math
from typing import Any
from uuid import UUID

import requests
from singer_sdk.exceptions import FatalAPIError

BASE_URL = "https://www.sanctionskit.com/api/v1"
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


class ExportError(FatalAPIError):
    """An export is incomplete; never treat previously emitted rows as a full snapshot."""


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("Duplicate JSON member")
        obj[key] = value
    return obj


class AccountClient:
    """Only GET /results and GET /audit; no configurable host or implicit credentials."""

    def __init__(self, api_key: str, timeout: int = 60) -> None:
        self._session = requests.Session()
        # Do not inherit .netrc credentials, proxies, or TLS overrides from the process.
        self._session.trust_env = False
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "tap-sanctionskit/0.1.0.dev0",
        })
        self._timeout = timeout

    def __enter__(self) -> AccountClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self._session.close()

    def page(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if path not in ("/results", "/audit"):
            raise ExportError("Unsupported export endpoint.")
        try:
            with self._session.get(
                BASE_URL + path,
                params=params,
                timeout=(10, self._timeout),
                allow_redirects=False,
                stream=True,
            ) as response:
                if response.status_code != 200:
                    status = response.status_code
                    if status in (401, 403):
                        detail = "Check account access and the results:read scope."
                    elif status == 429:
                        detail = "Rate limited; schedule a later full refresh."
                    elif 300 <= status < 400:
                        detail = "Redirects are refused to protect credentials."
                    else:
                        detail = "Check service availability and the documented configuration."
                    raise ExportError(f"SanctionsKit export failed (HTTP {status}). {detail}")
                chunks = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    chunks.extend(chunk)
                    if len(chunks) > MAX_RESPONSE_BYTES:
                        raise ExportError("Response exceeded the export size limit; reduce page_size.")
        except requests.RequestException:
            # URLs, headers and response bodies can contain private values.
            raise ExportError("SanctionsKit export transport failed; no automatic retry was made.") from None
        try:
            envelope = json.loads(
                chunks, parse_constant=_reject_constant, parse_float=_finite_float, object_pairs_hook=_unique_object
            )
        except (ValueError, UnicodeError, RecursionError):
            raise ExportError("SanctionsKit returned invalid JSON; the export is incomplete.") from None
        if not isinstance(envelope, dict) or "error" in envelope:
            raise ExportError("SanctionsKit returned an unexpected response envelope.")
        data = envelope.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list) or "nextCursor" not in data:
            raise ExportError("SanctionsKit returned an incomplete page envelope.")
        cursor = data["nextCursor"]
        if cursor is not None:
            if not isinstance(cursor, str):
                raise ExportError("SanctionsKit returned an invalid pagination cursor.")
            try:
                parsed = UUID(cursor)
                if str(parsed) != cursor.lower():
                    raise ValueError("Noncanonical UUID")
            except ValueError:
                raise ExportError("SanctionsKit returned an invalid pagination cursor.") from None
        return data
