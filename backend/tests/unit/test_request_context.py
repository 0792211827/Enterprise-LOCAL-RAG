"""Request correlation ID and client-address resolution."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from src.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from src.middleware.network import is_trusted, parse_networks, resolve_client_ip


def _app(trusted_proxies=()) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware, trusted_proxies=trusted_proxies)

    @app.get("/echo")
    def echo(request: Request):
        return {"request_id": request.state.request_id, "client_ip": request.state.client_ip}

    return app


class TestRequestId:
    def test_generated_when_absent(self):
        response = TestClient(_app()).get("/echo")
        rid = response.headers[REQUEST_ID_HEADER]
        assert len(rid) == 32
        assert response.json()["request_id"] == rid

    def test_valid_incoming_id_is_reused(self):
        response = TestClient(_app()).get("/echo", headers={REQUEST_ID_HEADER: "abc-123.X_y"})
        assert response.headers[REQUEST_ID_HEADER] == "abc-123.X_y"
        assert response.json()["request_id"] == "abc-123.X_y"

    @pytest.mark.parametrize("bad", ["has space", "x" * 129, "semi;colon", "new\tline"])
    def test_malformed_incoming_id_is_replaced(self, bad):
        response = TestClient(_app()).get("/echo", headers={REQUEST_ID_HEADER: bad})
        assert response.headers[REQUEST_ID_HEADER] != bad
        assert len(response.headers[REQUEST_ID_HEADER]) == 32

    def test_present_on_404(self):
        response = TestClient(_app()).get("/missing")
        assert response.status_code == 404
        assert REQUEST_ID_HEADER in response.headers


class TestClientIp:
    def test_forwarded_for_ignored_from_untrusted_peer(self):
        # TestClient's peer is "testclient", which is never a trusted address.
        response = TestClient(_app(["10.0.0.0/8"])).get("/echo", headers={"X-Forwarded-For": "1.2.3.4"})
        assert response.json()["client_ip"] == "testclient"

    def test_resolve_uses_peer_without_header(self):
        assert resolve_client_ip("10.0.0.5", None, parse_networks(["10.0.0.0/8"])) == "10.0.0.5"

    def test_resolve_ignores_header_from_untrusted_peer(self):
        assert resolve_client_ip("8.8.8.8", "1.2.3.4", parse_networks(["10.0.0.0/8"])) == "8.8.8.8"

    def test_resolve_returns_first_untrusted_hop_from_the_right(self):
        trusted = parse_networks(["10.0.0.0/8"])
        # client-spoofed, real client, then an inner trusted proxy
        assert resolve_client_ip("10.0.0.1", "6.6.6.6, 203.0.113.9, 10.0.0.2", trusted) == "203.0.113.9"

    def test_resolve_all_trusted_returns_leftmost(self):
        trusted = parse_networks(["10.0.0.0/8"])
        assert resolve_client_ip("10.0.0.1", "10.0.0.3, 10.0.0.2", trusted) == "10.0.0.3"


class TestNetworks:
    def test_parse_accepts_bare_addresses_and_ipv6(self):
        nets = parse_networks(["127.0.0.1", "10.0.0.0/8", "::1", " "])
        assert len(nets) == 3

    def test_parse_rejects_garbage(self):
        with pytest.raises(ValueError):
            parse_networks(["not-an-ip"])

    def test_is_trusted_handles_non_ip_peer(self):
        assert is_trusted("testclient", parse_networks(["10.0.0.0/8"])) is False
        assert is_trusted("10.1.2.3", []) is False
