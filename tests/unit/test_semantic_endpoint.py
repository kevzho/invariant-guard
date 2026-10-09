"""T41: loopback-only destination validation and transport (redirects, proxies, allowlist, limits)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from invariantguard.semantic.endpoint import EndpointError, LoopbackHttp, validate_loopback_endpoint
from invariantguard.semantic.ollama import OllamaReasoner

from ..model_helpers import FAKE_DIGEST, FakeOllama, loopback_only, unused_loopback_port  # noqa: F401 (fixture)


@pytest.mark.parametrize("url,ip", [
    ("http://127.0.0.1:11434", "127.0.0.1"),
    ("http://127.8.9.10:1", "127.8.9.10"),
    ("http://[::1]:11434", "::1"),
    ("http://localhost:11434", "127.0.0.1"),
    ("http://LOCALHOST:8080/", "127.0.0.1"),
    ("http://[::ffff:127.0.0.1]:11434", "::ffff:127.0.0.1"),
])
def test_loopback_endpoints_accepted(url, ip):
    ep = validate_loopback_endpoint(url)
    assert ep.ip == ip


@pytest.mark.parametrize("url,code", [
    ("http://10.0.0.1:11434", "NON_LOOPBACK"),
    ("http://192.168.1.5:11434", "NON_LOOPBACK"),
    ("http://0.0.0.0:11434", "NON_LOOPBACK"),
    ("http://[::]:11434", "NON_LOOPBACK"),
    ("http://[::ffff:8.8.8.8]:11434", "NON_LOOPBACK"),
    ("http://[fe80::1%25en0]:11434", "NON_LOOPBACK"),
    ("http://example.com:11434", "NON_LOOPBACK"),
    ("http://api.openai.com:443", "NON_LOOPBACK"),
    ("http://localhost.example.com:11434", "NON_LOOPBACK"),
    ("http://127.1:11434", "NON_LOOPBACK"),  # numeric shorthand is not accepted (never resolved)
    ("http://2130706433:11434", "NON_LOOPBACK"),
    ("http://0x7f000001:11434", "NON_LOOPBACK"),
    ("https://127.0.0.1:11434", "INVALID_ENDPOINT"),
    ("ftp://127.0.0.1:21", "INVALID_ENDPOINT"),
    ("http://user:pw@127.0.0.1:11434", "INVALID_ENDPOINT"),
    ("http://127.0.0.1:11434/api", "INVALID_ENDPOINT"),
    ("http://127.0.0.1:11434?x=1", "INVALID_ENDPOINT"),
    ("http://127.0.0.1:11434#f", "INVALID_ENDPOINT"),
    ("http://127.0.0.1", "INVALID_ENDPOINT"),
    ("http://127.0.0.1:99999", "INVALID_ENDPOINT"),
    ("http://127.0.0.1:0", "INVALID_ENDPOINT"),
    ("http://127.0.0.1:11434 ", "INVALID_ENDPOINT"),
    ("", "INVALID_ENDPOINT"),
    (None, "INVALID_ENDPOINT"),
])
def test_non_loopback_or_malformed_endpoints_rejected(url, code):
    with pytest.raises(EndpointError) as ei:
        validate_loopback_endpoint(url)
    assert ei.value.code == code


def test_config_rejects_remote_endpoint_and_downloads():
    from invariantguard.config import ConfigError, parse_config

    for text in (b'[semantic]\nenabled = true\nendpoint = "http://203.0.113.9:11434"\nmodel_id = "m"\n',
                 b'[semantic]\nendpoint = "http://example.com:11434"\n',  # rejected even while disabled
                 b'[semantic]\nenabled = true\nendpoint = "http://127.0.0.1:11434"\nmodel_id = "m"\ndownload_during_scan = true\n',
                 b'[semantic]\nenabled = true\nprovider = "openai"\nendpoint = "http://127.0.0.1:1"\nmodel_id = "m"\n',
                 b'[semantic]\nenabled = true\nendpoint = "http://127.0.0.1:1"\n',
                 b'[semantic]\nenabled = true\nendpoint = "http://127.0.0.1:1"\nmodel_id = "m"\nmodel_digest = "abc"\n',
                 b'[semantic]\nenabled = true\nendpoint = "http://127.0.0.1:1"\nmodel_id = "m"\nfallback = "cloud"\n'):
        with pytest.raises(ConfigError):
            parse_config(text)
    ok = parse_config(b'[semantic]\nenabled = true\nendpoint = "http://[::1]:11434"\nmodel_id = "m"\n')
    assert ok.semantic["enabled"] and ok.semantic["provider"] == "loopback"


def _server(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def test_redirect_is_rejected_and_never_followed(loopback_only):
    hits = []

    class Target(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"version": "x"}')

    target = _server(Target)
    try:
        # The redirect even points at another loopback server: it must still not be followed.
        with FakeOllama(redirect_to=f"http://127.0.0.1:{target.server_address[1]}/api/version") as fake:
            http = LoopbackHttp(validate_loopback_endpoint(fake.url), 5)
            with pytest.raises(EndpointError) as ei:
                http.request("GET", "/api/version")
            assert ei.value.code == "REDIRECT_REJECTED"
            caps = OllamaReasoner(validate_loopback_endpoint(fake.url), "fake-model:1b").capabilities()
            assert not caps.available and "REDIRECT_REJECTED" in caps.reason
        assert hits == []
    finally:
        target.shutdown()
        target.server_close()


def test_proxy_environment_is_ignored(loopback_only, monkeypatch):
    proxy_hits = []

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            proxy_hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

    proxy = _server(Proxy)
    try:
        url = f"http://127.0.0.1:{proxy.server_address[1]}"
        for var in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(var, url)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        with FakeOllama() as fake:
            r = LoopbackHttp(validate_loopback_endpoint(fake.url), 5).request("GET", "/api/version")
            assert r.body == {"version": "0.0.0-fake"}
        assert proxy_hits == []
    finally:
        proxy.shutdown()
        proxy.server_close()


def test_request_allowlist_has_no_model_download(loopback_only):
    with FakeOllama() as fake:
        http = LoopbackHttp(validate_loopback_endpoint(fake.url), 5)
        for method, path in (("POST", "/api/pull"), ("POST", "/api/create"), ("DELETE", "/api/delete"), ("POST", "/api/push"),
                             ("POST", "/api/generate"), ("POST", "/v1/chat/completions")):
            with pytest.raises(EndpointError) as ei:
                http.request(method, path, {"model": "x"})
            assert ei.value.code == "REQUEST_NOT_ALLOWED"
        assert fake.requests == []  # rejected before any connection


def test_unreachable_oversized_and_absent_model(loopback_only):
    port = unused_loopback_port()
    caps = OllamaReasoner(validate_loopback_endpoint(f"http://127.0.0.1:{port}"), "m").capabilities()
    assert not caps.available and caps.reason.startswith("UNREACHABLE")
    with FakeOllama(oversized=True) as fake:
        with pytest.raises(EndpointError) as ei:
            LoopbackHttp(validate_loopback_endpoint(fake.url), 5).request("GET", "/api/version")
        assert ei.value.code == "RESPONSE_TOO_LARGE"
    with FakeOllama(models_present=False) as fake:
        caps = OllamaReasoner(validate_loopback_endpoint(fake.url), "fake-model:1b").capabilities()
        assert not caps.available and caps.reason.startswith("MODEL_ABSENT") and "never downloads" in caps.reason
        assert all(p != "/api/pull" for _, p, _ in fake.requests)


def test_identity_is_read_from_the_runtime_and_digest_pin_enforced(loopback_only):
    with FakeOllama(version="9.9.9") as fake:
        ep = validate_loopback_endpoint(fake.url)
        caps = OllamaReasoner(ep, "fake-model:1b", pinned_digest="sha256:" + FAKE_DIGEST).capabilities()
        assert caps.available and caps.structured_output == "json_schema" and caps.operations == ("propose_contracts",)
        ident = caps.identity
        assert ident.revision == "sha256:" + FAKE_DIGEST and ident.runtime == "ollama 9.9.9" and ident.quantization == "Q4_K_M"
        assert ident.license.startswith("fake-1.0 (Fake License 1.0; license text sha256:")
        assert caps.context_limit_tokens == 4096
        bad = OllamaReasoner(ep, "fake-model:1b", pinned_digest="cd" * 32).capabilities()
        assert not bad.available and bad.reason.startswith("DIGEST_MISMATCH")
    assert all(a[0] == "127.0.0.1" for a in loopback_only)


def test_chat_request_uses_json_schema_format_and_no_tools(loopback_only):
    from invariantguard.semantic.context import build_context_packet
    from invariantguard.semantic.prompt import render_prompt

    src = {"m.py": b"def f(xs: list[int]) -> list[int]:\n    return sorted(xs)\n"}
    bp = build_context_packet(src, "sha256:" + "0" * 64, "m.py::f", "bounded_builtin_int_list_v1")
    with FakeOllama(chat=lambda sym, body: json.dumps({"candidates": []})) as fake:
        r = OllamaReasoner(validate_loopback_endpoint(fake.url), "fake-model:1b").propose_contracts(render_prompt(bp))
        (body,) = fake.chats()
    assert r.text == '{"candidates": []}' and r.prompt_tokens == 100 and r.output_tokens == 50
    assert body["stream"] is False and isinstance(body["format"], dict) and body["format"]["required"] == ["candidates"]
    assert "tools" not in body and body["options"]["temperature"] == 0.0
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
