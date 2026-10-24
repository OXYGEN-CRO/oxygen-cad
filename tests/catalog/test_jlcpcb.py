"""Offline JLCAPI contract fixtures; these do not establish vendor access."""
import base64
import hmac
import json
import re

import httpx
import pytest

from o3d.catalog import jlcpcb
from o3d.catalog.jlcpcb import JLCPCBClient, JLCPCBConfigurationError, JLCPCBError, JLCPCBPermissionError


def client(handler):
    return JLCPCBClient("fixture-app", "fixture-access", "fixture-secret",
                        transport=httpx.MockTransport(handler))


def test_exact_wire_body_and_independent_signature_vector(monkeypatch):
    monkeypatch.setattr(jlcpcb.time, "time", lambda: 1700000000)
    monkeypatch.setattr(jlcpcb.secrets, "token_hex", lambda n: "0123456789abcdef0123456789abcdef")
    # This vector is constructed independently of the adapter's auth method.
    canonical = (b"POST\n/overseas/openapi/component/getComponentDetailByCode\n1700000000\n"
                 b"0123456789abcdef0123456789abcdef\n"
                 b'{"componentCodes":["C25804","C8734"]}\n')
    expected = base64.b64encode(hmac.digest(b"fixture-secret", canonical, "sha256")).decode()
    # Also cross-checked with openssl dgst -sha256 -mac HMAC (fixture key).
    assert expected == "N/ifQtM8DzuxOIgXHYZuZU98M+ytwjZ5Jh90He08kd4="

    def handler(request):
        assert str(request.url) == jlcpcb.ENDPOINT
        assert request.method == "POST"
        assert request.content == b'{"componentCodes":["C25804","C8734"]}'
        assert request.headers["Content-Type"] == "application/json"
        assert request.headers["Authorization"] == (
            'JOP appid="fixture-app",accesskey="fixture-access",'
            'nonce="0123456789abcdef0123456789abcdef",timestamp="1700000000",'
            f'signature="{expected}"')
        return httpx.Response(200, json={"code": 200, "data": [{"componentCode": "C25804", "stockCount": 3}]})

    with client(handler) as api:
        assert api.get_component_details(["C25804", "C8734"]) == [{"componentCode": "C25804", "stockCount": 3}]
        assert api.configuration_status()["real_authenticated_verified"] is False


def test_default_nonce_and_timestamp_are_well_formed():
    def handler(request):
        auth = request.headers["Authorization"]
        assert re.search(r'nonce="[0-9a-f]{32}"', auth)
        assert re.search(r'timestamp="[0-9]{10,}"', auth)
        return httpx.Response(200, json={"code": 200, "data": []})
    with client(handler) as api:
        assert api.get_component_details(["C25804"]) == []


def test_configuration_status_without_credentials_does_not_call_network():
    def forbidden(request):
        pytest.fail("Missing credentials must fail before network access")
    with JLCPCBClient.from_env({}, transport=httpx.MockTransport(forbidden)) as api:
        status = api.configuration_status()
        assert status["configured"] is False
        assert status["missing_credentials"] == ["JLCPCB_APP_ID", "JLCPCB_ACCESS_KEY", "JLCPCB_SECRET_KEY"]
        assert status["endpoint"] == jlcpcb.ENDPOINT
        assert status["docs_url"] == "https://api.jlcpcb.com/docs/start"
        assert status["real_authenticated_verified"] is False
        with pytest.raises(JLCPCBConfigurationError, match="Missing JLCPCB credentials"):
            api.get_component_details(["C25804"])
        assert api.get_component_details([]) == []


def test_env_primary_names_and_aliases_and_precedence():
    for values in [
        {"JLCPCB_APP_ID": "app", "JLCPCB_ACCESS_KEY": "access", "JLCPCB_SECRET_KEY": "secret"},
        {"JLCPCB_APP_ID": "app", "JLCPCB_API_KEY": "access", "JLCPCB_API_SECRET": "secret"},
        {"JLCPCB_APP_ID": "app", "JLCPCB_ACCESS_KEY": "access", "JLCPCB_SECRET_KEY": "secret",
         "JLCPCB_API_KEY": "wrong", "JLCPCB_API_SECRET": "wrong"},
    ]:
        def handler(request):
            assert 'accesskey="access"' in request.headers["Authorization"]
            return httpx.Response(200, json={"code": 200, "data": []})
        with JLCPCBClient.from_env(values, transport=httpx.MockTransport(handler)) as api:
            assert api.configuration_status()["configured"] is True
            assert api.configuration_status()["missing_credentials"] == []
            api.get_component_details(["C1"])


def test_partial_status_and_redacted_repr():
    with JLCPCBClient("private-app", None, "private-secret") as api:
        assert api.configuration_status()["missing_credentials"] == ["JLCPCB_ACCESS_KEY"]
        output = repr(api) + json.dumps(api.configuration_status())
        with pytest.raises(JLCPCBConfigurationError) as error:
            api.get_component_details(["C1"])
        output += str(error.value)
        assert "private-app" not in output
        assert "private-secret" not in output


@pytest.mark.parametrize("values", [("a\r\nb", "key", "secret"), ('a"', "key", "secret"),
                                    ("app", "key,other", "secret"), ("app", "key", "secret\x00")])
def test_unsafe_credentials_are_rejected_without_echo(values):
    with pytest.raises(JLCPCBConfigurationError) as error:
        JLCPCBClient(*values)
    assert "secret" not in str(error.value)
    assert "other" not in str(error.value)


@pytest.mark.parametrize("codes", ["C25804", ["25804"], ["c25804"], ["C1\n"], ["C1/../../"],
                                  [1], [None], ["C1234567890123"], ["C1"] * 1001])
def test_invalid_codes_fail_without_network(codes):
    def forbidden(request):
        pytest.fail("Invalid codes must not reach the network")
    with client(forbidden) as api, pytest.raises(ValueError):
        api.get_component_details(codes)


def test_maximum_batch_is_one_bounded_request():
    calls = []
    def handler(request):
        calls.append(request)
        assert len(json.loads(request.content)["componentCodes"]) == 1000
        return httpx.Response(200, json={"code": 200, "data": []})
    with client(handler) as api:
        assert api.get_component_details([f"C{i}" for i in range(1000)]) == []
    assert len(calls) == 1


@pytest.mark.parametrize("status", [301, 302, 307, 400, 401, 403, 429, 500])
def test_http_errors_and_redirects_are_sanitized_and_not_followed(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://attacker.invalid/"},
                              text="private-secret private-access")
    with client(handler) as api, pytest.raises(JLCPCBError) as error:
        api.get_component_details(["C1"])
    assert len(calls) == 1
    assert f"HTTP {status}" in str(error.value)
    assert "private" not in str(error.value)


def test_transport_error_is_sanitized(caplog):
    def handler(request):
        raise httpx.ConnectError("fixture-secret " + request.headers["Authorization"], request=request)
    with client(handler) as api, pytest.raises(JLCPCBError) as error:
        api.get_component_details(["C1"])
    assert str(error.value) == "JLCPCB component lookup transport failed"
    assert error.value.__suppress_context__ is True
    assert "fixture-secret" not in caplog.text
    assert "fixture-access" not in caplog.text


@pytest.mark.parametrize("payload", [[], {"code": 401, "message": "fixture-secret"},
                                    {"code": "200", "data": []}, {"success": True, "data": []},
                                    {"code": 200}, {"code": 200, "data": {}},
                                    {"code": 200, "data": ["bad"]},
                                    {"code": 200, "data": [{"componentCode": "C2"}]},
                                    {"code": 200, "data": [{"componentCode": "C1"}] * 2}])
def test_api_failures_and_unexpected_shapes_never_become_success(payload):
    with client(lambda request: httpx.Response(200, json=payload)) as api:
        with pytest.raises(JLCPCBError) as error:
            api.get_component_details(["C1"])
        assert "fixture-secret" not in str(error.value)
        assert api.configuration_status()["real_authenticated_verified"] is False


def test_wrapped_response_fixture():
    payload = {"code": 200, "data": {"componentDetailResponseVOList": [{"componentCode": "C1", "priceRanges": []}]}}
    with client(lambda request: httpx.Response(200, json=payload)) as api:
        assert api.get_component_details(["C1"]) == payload["data"]["componentDetailResponseVOList"]


def test_invalid_json_is_sanitized():
    with client(lambda request: httpx.Response(200, text="fixture-secret")) as api:
        with pytest.raises(JLCPCBError, match="invalid JSON") as error:
            api.get_component_details(["C1"])
        assert "fixture-secret" not in str(error.value)


def test_response_byte_limit(monkeypatch):
    monkeypatch.setattr(jlcpcb, "MAX_RESPONSE_BYTES", 8)
    with client(lambda request: httpx.Response(200, content=b"x" * 9)) as api:
        with pytest.raises(JLCPCBError, match="size limit"):
            api.get_component_details(["C1"])


@pytest.mark.parametrize("http_status", [200, 403])
def test_scope_denial_matches_actual_vendor_response_without_exposing_body(http_status):
    # Observed live 2026-10-24; no credentials or private records in this fixture.
    response = {"code": 403, "message": "API insufficient permissions, access denied", "success": False}
    with client(lambda request: httpx.Response(http_status, json=response)) as api:
        with pytest.raises(JLCPCBPermissionError) as error:
            api.get_component_details(["C25804"])
        assert error.value.http_status == http_status
        assert error.value.api_code == 403
        assert "Components API scope" in str(error.value)
        status = api.configuration_status()
        assert status["real_authenticated_verified"] is False
        assert status["last_request"] == {
            "endpoint": jlcpcb.ENDPOINT, "http_status": http_status, "api_code": 403,
            "outcome": "permission_denied", "returned_items": 0, "real_network": False}


def test_denial_preserves_prior_verification_and_status_is_a_copy():
    with client(lambda request: httpx.Response(403, json={"code": 403})) as api:
        # Simulate historical success; injected transport itself never verifies.
        api._real_authenticated_verified = True
        with pytest.raises(JLCPCBPermissionError):
            api.get_component_details(["C1"])
        status = api.configuration_status()
        status["last_request"]["outcome"] = "tampered"
        assert api.configuration_status()["last_request"]["outcome"] == "permission_denied"
        assert api.configuration_status()["real_authenticated_verified"] is True


@pytest.mark.parametrize("method,args,path,body", [
    ("get_component_library_page", {}, jlcpcb.LIBRARY_PATH, b'{"currentPage":1,"pageSize":30}'),
    ("get_component_library_page", {"current_page": 2, "page_size": 1}, jlcpcb.LIBRARY_PATH,
     b'{"currentPage":2,"pageSize":1}'),
    ("get_component_feed", {}, jlcpcb.FEED_PATH, b'{}'),
    ("get_component_feed", {"last_key": "opaque-cursor"}, jlcpcb.FEED_PATH, b'{"lastKey":"opaque-cursor"}'),
])
def test_catalog_reads_exact_single_request_with_their_own_signed_path(method, args, path, body, monkeypatch):
    monkeypatch.setattr(jlcpcb.time, "time", lambda: 1700000000)
    monkeypatch.setattr(jlcpcb.secrets, "token_hex", lambda n: "0" * 32)
    calls = []
    payload = {"records": [{"componentCode": "C1"}], "lastKey": "next"}
    def handler(request):
        calls.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://open.jlcpcb.com" + path
        assert request.content == body
        canonical = b"POST\n" + path.encode() + b"\n1700000000\n" + b"0" * 32 + b"\n" + body + b"\n"
        signature = base64.b64encode(hmac.digest(b"fixture-secret", canonical, "sha256")).decode()
        assert f'signature="{signature}"' in request.headers["Authorization"]
        return httpx.Response(200, json={"code": 200, "data": payload})
    with client(handler) as api:
        assert getattr(api, method)(**args) == payload
        assert api.configuration_status()["last_request"]["outcome"] == "success"
        assert api.configuration_status()["last_request"]["returned_items"] is None
        assert api.configuration_status()["real_authenticated_verified"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("args", [{"current_page": 0}, {"current_page": True}, {"current_page": 1_000_001},
                                 {"page_size": 0}, {"page_size": 101}, {"page_size": 1.0}])
def test_catalog_page_bounds(args):
    with client(lambda request: pytest.fail("Invalid page input reached network")) as api:
        with pytest.raises(ValueError):
            api.get_component_library_page(**args)


@pytest.mark.parametrize("cursor", ["", "x" * 2049, "a\nb", "x\x00", 1, []])
def test_feed_cursor_bounds(cursor):
    with client(lambda request: pytest.fail("Invalid cursor reached network")) as api:
        with pytest.raises(ValueError):
            api.get_component_feed(cursor)


@pytest.mark.parametrize("method", ["get_component_library_page", "get_component_feed"])
def test_catalog_scope_denials_and_malformed_data(method):
    for payload in [{"code": 403, "message": "fixture-secret"}, {"code": 200, "data": None}]:
        with client(lambda request: httpx.Response(200, json=payload)) as api:
            with pytest.raises(JLCPCBError) as error:
                getattr(api, method)()
            assert "fixture-secret" not in str(error.value)
            assert api.configuration_status()["real_authenticated_verified"] is False


def test_internal_read_endpoint_allowlist():
    with client(lambda request: pytest.fail("Disallowed endpoint reached network")) as api:
        with pytest.raises(ValueError, match="allowlist"):
            api._request_data("/overseas/openapi/pcb/createOrder", {})
