"""TIPSA shipment-page client.

Two keyless GETs per refresh: the postcode-gated lookup door, which answers
200 with the detail URL in a body meta-refresh, then the detail page itself.

The contract the coordinator relies on:

* ``async_get_parcel`` returns the raw per-parcel dict on success,
* returns ``None`` when the carrier answers "no such shipment" (wrong
  postcode, unknown tracking_code and expired history are one indistinguishable
  answer, a normal and non-retryable state),
* raises :class:`TIPSAApiError` for anything else, with ``status_code`` set on
  a non-2xx response and ``retry_after`` on a 429,
* lets ``aiohttp.ClientError`` propagate untouched.

Error messages never carry the tracking_code, the postcode or a URL: the first
request URL holds the postcode and the second a shipment id.
"""
from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import aiohttp

from .const import (
    DETAIL_ENCODING,
    DETAIL_ORIGIN,
    DETAIL_PATH,
    LOOKUP_URL,
    NEW_BUG_ISSUE_URL,
    REQUEST_TIMEOUT_SECONDS,
)
from .parser import TIPSAParseError, parse_page

_LOGGER = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}
_UUID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{2}$")
_MISSING_POSTCODE = "No ha proporcionado el CP"
_MALFORMED_POSTCODE = "Error en el CP"
# Cloudflare also injects a harmless ``challenge-platform`` script into normal
# pages, so only markers unique to an interstitial count.
_CHALLENGE_MARKERS = ("<title>Just a moment", "cf-browser-verification", "cf_chl_opt")


class TIPSAApiError(Exception):
    """Raised when a TIPSA request returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"TIPSA request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class _RefreshParser(HTMLParser):
    """Find the target of a ``<meta http-equiv="refresh">``."""

    def __init__(self) -> None:
        super().__init__()
        self.target: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag != "meta" or self.target is not None:
            return
        attr = dict(attrs)
        if (attr.get("http-equiv") or "").lower() != "refresh":
            return
        match = re.search(r"url\s*=\s*(.+)$", attr.get("content") or "", re.I)
        if match:
            self.target = match.group(1).strip().strip("'\"")


def normalize_postcode(value: str) -> str:
    """Strip all whitespace and upper-case; a surviving space is rejected."""
    return re.sub(r"\s+", "", value or "").upper()


def _checked_detail_url(target: str | None) -> tuple[str, bool]:
    """Validate the refresh target; return ``(url, known)``.

    ``known`` is False for the not-found shape (both params empty). Anything
    that is not exactly the expected detail route is a changed page.
    """
    if not target:
        raise TIPSAApiError("lookup answered without a refresh target")
    try:
        parts = urlsplit(target)
        params = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError as err:
        raise TIPSAApiError("lookup refresh target is malformed") from err
    names = sorted(name for name, _ in params)
    if (
        f"{parts.scheme}://{parts.netloc}" != DETAIL_ORIGIN
        or parts.path != DETAIL_PATH
        or parts.fragment
        or names != ["fecha", "servicio"]
    ):
        raise TIPSAApiError("lookup refreshed to an unexpected address")
    values = dict(params)
    servicio, fecha = values["servicio"], values["fecha"]
    if not servicio and not fecha:
        return target, False
    if not _UUID_RE.match(servicio) or not _DATE_RE.match(fecha):
        raise TIPSAApiError("lookup refresh parameters have an unexpected shape")
    return target, True


class TIPSAApiClient:
    """Client for TIPSA's keyless, postcode-gated shipment page."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session
        # Postcodes the gate has already rejected, so a hub whose postcode
        # shape this carrier does not accept warns once instead of every poll.
        self._rejected_postcodes: set[str] = set()

    async def _get(self, url: str, encoding: str, **kwargs: Any) -> str:
        """GET ``url`` and return the body, mapping HTTP failures to errors."""
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        async with self._session.get(
            url,
            headers=_HEADERS,
            timeout=timeout,
            allow_redirects=False,
            **kwargs,
        ) as response:
            if response.status == 429:
                header = response.headers.get("Retry-After")
                try:
                    retry_after = float(header) if header else None
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds
                raise TIPSAApiError("HTTP 429", status_code=429, retry_after=retry_after)
            if response.status != 200:
                raise TIPSAApiError(f"HTTP {response.status}", status_code=response.status)
            body = await response.read()
        return body.decode(encoding, errors="replace")

    async def async_get_parcel(
        self, tracking_code: str, postal_code: str
    ) -> dict[str, Any] | None:
        """Fetch one parcel's details.

        Returns the parsed parcel for a known shipment, or ``None`` when TIPSA
        reports it as unknown. Never retries with another postcode and never
        falls back to a postcode-free route.
        """
        postal_code = normalize_postcode(postal_code)
        if not postal_code:
            raise TIPSAApiError("no postcode configured")

        lookup = await self._get(
            LOOKUP_URL, "utf-8", params={"id": tracking_code, "cp": postal_code}
        )
        if _MISSING_POSTCODE in lookup or _MALFORMED_POSTCODE in lookup:
            if postal_code not in self._rejected_postcodes:
                self._rejected_postcodes.add(postal_code)
                # Spanish postcodes are the only shape confirmed through the
                # gate, so a Portuguese or Andorran one landing here is the
                # carrier disagreeing with us, not the user mistyping.
                _LOGGER.warning(
                    "TIPSA would not accept this hub's postal code. If it is "
                    "Spanish this is a bug in the integration — please open an "
                    "issue. Portuguese and Andorran postal codes have never "
                    "been confirmed through TIPSA's lookup, so an issue naming "
                    "the country and the postal code's format is exactly what "
                    "is needed to fix it: %s",
                    NEW_BUG_ISSUE_URL,
                )
            raise TIPSAApiError("lookup rejected the postcode as missing or malformed")

        refresh = _RefreshParser()
        refresh.feed(lookup)
        refresh.close()
        url, known = _checked_detail_url(refresh.target)
        if not known:
            return None

        # The detail response declares no charset; the body is Latin-1.
        detail = await self._get(url, DETAIL_ENCODING)
        if any(marker in detail for marker in _CHALLENGE_MARKERS):
            raise TIPSAApiError("detail page served a bot challenge")
        try:
            return parse_page(detail, tracking_code)
        except TIPSAParseError as err:
            raise TIPSAApiError(str(err)) from err
