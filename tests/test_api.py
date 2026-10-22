"""Tests for the TIPSA API client."""

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.tipsa.api import (
    TIPSAApiClient,
    TIPSAApiError,
    normalize_postcode,
)
from custom_components.tipsa.const import LOOKUP_URL

from .payloads import (
    DELIVERED_CODE,
    DETAIL_URL,
    EMPTY_TARGET,
    FORBIDDEN_VALUES,
    POSTCODE,
    SERVICIO,
    detail_bytes,
    lookup_html,
)


def _response(status=200, body=b"", headers=None):
    response = AsyncMock()
    response.status = status
    response.headers = headers or {}
    response.read = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _session(*responses):
    session = MagicMock()
    session.get = MagicMock(side_effect=list(responses))
    return session


def _lookup(target):
    return _response(body=lookup_html(target).encode())


async def test_get_parcel_runs_both_hops_and_parses():
    session = _session(_lookup(DETAIL_URL), _response(body=detail_bytes()))
    client = TIPSAApiClient(session)

    parcel = await client.async_get_parcel(DELIVERED_CODE, POSTCODE)

    assert parcel["tracking_code"] == DELIVERED_CODE
    assert parcel["status"] == "ENTREGADO"
    first, second = session.get.call_args_list
    assert first.args[0] == LOOKUP_URL
    assert first.kwargs["params"] == {"id": DELIVERED_CODE, "cp": POSTCODE}
    assert second.args[0] == DETAIL_URL
    for call in (first, second):
        assert call.kwargs["allow_redirects"] is False
        assert call.kwargs["headers"]["Accept-Language"].startswith("es-ES")
        assert "Cookie" not in call.kwargs["headers"]
        assert "Referer" not in call.kwargs["headers"]
        assert "User-Agent" not in call.kwargs["headers"]


async def test_postcode_is_stripped_of_all_whitespace_before_sending():
    session = _session(_lookup(DETAIL_URL), _response(body=detail_bytes()))
    await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, " 28 001\n")
    assert session.get.call_args_list[0].kwargs["params"]["cp"] == "28001"


def test_normalize_postcode():
    assert normalize_postcode(" ad 500 ") == "AD500"
    assert normalize_postcode(None) == ""


async def test_the_detail_body_is_decoded_as_latin_1():
    """Accented place names are mojibake if the charset is guessed."""
    rows = [("17/04/26 20:19", "ENTREGADO", "SANT JOAN DESPÍ")]
    body = detail_bytes(rows=rows)
    assert "Í".encode() not in body  # truly Latin-1 on the wire
    session = _session(_lookup(DETAIL_URL), _response(body=body))

    parcel = await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, POSTCODE)

    assert parcel["events"][0]["location"] == "SANT JOAN DESPÍ"


async def test_empty_servicio_is_not_found_and_stops_after_one_request():
    """Wrong postcode, unknown tracking_code and expired history are one answer."""
    session = _session(_lookup(EMPTY_TARGET))
    client = TIPSAApiClient(session)

    assert await client.async_get_parcel(DELIVERED_CODE, POSTCODE) is None
    assert session.get.call_count == 1  # no detail fetch, no second postcode


async def test_never_falls_back_to_the_postcode_free_door():
    session = _session(_lookup(EMPTY_TARGET))
    await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, POSTCODE)
    assert all("datos_env" not in str(call) for call in session.get.call_args_list)


@pytest.mark.parametrize(
    "body", ["No ha proporcionado el CP", "<h1>Error en el CP</h1>"]
)
async def test_rejected_postcode_is_our_bug_and_logs_no_values(body, caplog):
    session = _session(_response(body=body.encode()))

    with pytest.raises(TIPSAApiError) as err:
        await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, POSTCODE)

    assert "postcode" in err.value.detail
    logged = caplog.text + str(err.value)
    assert DELIVERED_CODE not in logged
    assert POSTCODE not in logged


async def test_a_rejected_postcode_warns_once_not_every_poll(caplog):
    """A hub whose postcode shape the gate dislikes fails every poll forever."""
    body = b"No ha proporcionado el CP"
    client = TIPSAApiClient(_session(_response(body=body), _response(body=body)))

    for _ in range(2):
        with pytest.raises(TIPSAApiError):
            await client.async_get_parcel(DELIVERED_CODE, POSTCODE)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    # Portuguese and Andorran postcodes have never been through the gate, so
    # the wording must not tell those users it is our bug outright.
    assert "Portuguese and Andorran" in warnings[0].getMessage()


async def test_no_postcode_raises_without_a_request():
    session = _session()
    with pytest.raises(TIPSAApiError):
        await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, "  ")
    session.get.assert_not_called()


@pytest.mark.parametrize(
    "target",
    [
        None,
        "https://evil.example/dinapaqweb/detalle_envio.php?servicio=&fecha=",
        "http://dinapaqweb.tipsa-dinapaq.com/dinapaqweb/detalle_envio.php?servicio=&fecha=",
        "https://user@dinapaqweb.tipsa-dinapaq.com/dinapaqweb/detalle_envio.php?servicio=&fecha=",
        "https://dinapaqweb.tipsa-dinapaq.com/other.php?servicio=&fecha=",
        DETAIL_URL + "#frag",
        DETAIL_URL + "&extra=1",
        DETAIL_URL.replace("&fecha=15/04/26", ""),
        DETAIL_URL.replace(SERVICIO, "not-a-uuid"),
        DETAIL_URL.replace("15/04/26", "2026-04-15"),
        "https://dinapaqweb.tipsa-dinapaq.com/dinapaqweb/detalle_envio.php?servicio=&fecha=15/04/26",
        "https://[::1/dinapaqweb/detalle_envio.php",
    ],
)
async def test_unexpected_refresh_target_is_a_changed_route(target):
    session = _session(_lookup(target))
    with pytest.raises(TIPSAApiError):
        await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, POSTCODE)
    assert session.get.call_count == 1  # the bad target is never fetched


async def test_refresh_target_is_found_with_double_quotes_and_odd_case():
    body = lookup_html(DETAIL_URL).replace("'", '"').replace("refresh", "Refresh")
    session = _session(_response(body=body.encode()), _response(body=detail_bytes()))
    assert await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, POSTCODE)


async def test_meta_without_a_url_is_a_changed_route():
    body = "<meta http-equiv='refresh' content='5'><meta name='x' content='y'>"
    session = _session(_response(body=body.encode()))
    with pytest.raises(TIPSAApiError):
        await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, POSTCODE)


async def test_identity_mismatch_is_never_published():
    session = _session(
        _lookup(DETAIL_URL), _response(body=detail_bytes("0000900000900000000009"))
    )
    with pytest.raises(TIPSAApiError, match="does not match"):
        await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, POSTCODE)


async def test_changed_detail_page_raises():
    session = _session(_lookup(DETAIL_URL), _response(body=detail_bytes(table=False)))
    with pytest.raises(TIPSAApiError):
        await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, POSTCODE)


async def test_cloudflare_interstitial_is_a_transport_failure():
    session = _session(
        _lookup(DETAIL_URL),
        _response(body=b"<html><head><title>Just a moment...</title></head></html>"),
    )
    with pytest.raises(TIPSAApiError, match="challenge"):
        await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, POSTCODE)


async def test_the_cloudflare_beacon_on_a_normal_page_is_not_a_challenge():
    page = detail_bytes().replace(
        b"</body>",
        b"<script src='/cdn-cgi/challenge-platform/h/b/scripts/jsd/x'></script></body>",
    )
    session = _session(_lookup(DETAIL_URL), _response(body=page))
    assert await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, POSTCODE)


async def test_error_messages_never_carry_identifiers():
    session = _session(
        _lookup(DETAIL_URL), _response(body=detail_bytes("0000900000900000000009"))
    )
    with pytest.raises(TIPSAApiError) as err:
        await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, POSTCODE)
    text = str(err.value)
    for secret in (DELIVERED_CODE, POSTCODE, SERVICIO, *FORBIDDEN_VALUES):
        assert secret not in text


@pytest.mark.parametrize("hop", [0, 1])
async def test_http_error_carries_the_status(hop):
    responses = [_lookup(DETAIL_URL), _response(500)]
    if hop == 0:
        responses = [_response(503)]
    with pytest.raises(TIPSAApiError) as err:
        await TIPSAApiClient(_session(*responses)).async_get_parcel(
            DELIVERED_CODE, POSTCODE
        )
    assert err.value.status_code in (500, 503)


async def test_redirect_from_the_lookup_is_not_followed():
    session = _session(
        _response(302, headers={"Location": "https://elsewhere.example/"})
    )
    with pytest.raises(TIPSAApiError) as err:
        await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, POSTCODE)
    assert err.value.status_code == 302


async def test_429_surfaces_retry_after_seconds():
    session = _session(_response(429, headers={"Retry-After": "120"}))
    with pytest.raises(TIPSAApiError) as err:
        await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, POSTCODE)
    assert err.value.status_code == 429
    assert err.value.retry_after == 120.0


@pytest.mark.parametrize("header", [None, "Wed, 21 Oct 2026 07:28:00 GMT"])
async def test_429_without_usable_retry_after(header):
    headers = {"Retry-After": header} if header else {}
    session = _session(_response(429, headers=headers))
    with pytest.raises(TIPSAApiError) as err:
        await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, POSTCODE)
    assert err.value.retry_after is None


async def test_network_error_propagates():
    """ClientError is left alone, DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    with pytest.raises(aiohttp.ClientError):
        await TIPSAApiClient(session).async_get_parcel(DELIVERED_CODE, POSTCODE)
