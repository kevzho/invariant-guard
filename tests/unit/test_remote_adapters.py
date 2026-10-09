"""Bring-your-own-key adapters (DECISIONS I-105): destination policy, transport limits, secrets, structured output.

Driven against a fake provider on 127.0.0.1 (tests/remote_helpers.py). No hosted provider is contacted.
"""

import json
import os
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from invariantguard.config import ConfigError, parse_config
from invariantguard.semantic.prompt import response_schema
from invariantguard.semantic.reasoner import Prompt, ReasonerUnavailable
from invariantguard.semantic.remote import (AnthropicReasoner, DestinationError, OpenAICompatibleReasoner, make_remote_reasoner, unroll_schema,
                                            validate_provider_url)

from ..model_helpers import loopback_only, unused_loopback_port  # noqa: F401 (fixture)
from ..remote_helpers import KEY_ENV, SECRET, FakeProvider


@pytest.fixture(autouse=True)
def _loopback(loopback_only):
    """Only loopback connections are possible in this module (and every one is recorded)."""
    return loopback_only


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(KEY_ENV, SECRET)
    return SECRET


def _prompt() -> Prompt:
    schema = response_schema(("xs",), "bounded_builtin_numeric_list_v1", 1, 3)
    return Prompt(system="S: answer with JSON", user="TASK: propose up to 3 contracts for demo/m.py::f.\nbody", response_schema=schema,
                  prompt_version="t", digest="sha256:" + "0" * 64)


@pytest.mark.parametrize("url,allowed,host,loop", [
    ("http://127.0.0.1:11434/v1", False, "127.0.0.1", True),
    ("http://localhost:8080/v1", False, "localhost", True),
    ("http://[::1]:9/v1", False, "::1", True),
    ("https://api.anthropic.com", True, "api.anthropic.com", False),
    ("https://llm.internal.example:8443/v1/", True, "llm.internal.example", False),
    ("https://203.0.113.7/v1", True, "203.0.113.7", False),
])
def test_destinations_accepted(url, allowed, host, loop):
    d = validate_provider_url(url, remote_allowed=allowed)
    assert d.host == host and d.loopback is loop
    assert d.scheme == ("https" if url.startswith("https") else "http")


@pytest.mark.parametrize("url,allowed,code", [
    ("http://api.openai.com/v1", True, "INSECURE_REMOTE"),  # plain http to a non-loopback host, even when remote is allowed
    ("http://10.0.0.5:8000/v1", True, "INSECURE_REMOTE"),
    ("https://api.openai.com/v1", False, "REMOTE_NOT_ALLOWED"),
    ("https://203.0.113.7/v1", False, "REMOTE_NOT_ALLOWED"),
    ("https://user:pw@api.example.com/v1", True, "INVALID_ENDPOINT"),
    ("https://api.example.com/v1?k=1", True, "INVALID_ENDPOINT"),
    ("https://api.example.com/v1#x", True, "INVALID_ENDPOINT"),
    ("https://api.example.com/../v1", True, "INVALID_ENDPOINT"),
    ("ftp://api.example.com", True, "INVALID_ENDPOINT"),
    ("https://0x7f000001/v1", True, "INVALID_ENDPOINT"),
    ("https://127.1/v1", True, "INVALID_ENDPOINT"),
    ("https://2130706433/v1", True, "INVALID_ENDPOINT"),
    ("https://0.0.0.0/v1", True, "INVALID_ENDPOINT"),
    ("https://foo.localhost/v1", True, "NON_LOOPBACK"),
    ("http://127.0.0.1/v1", False, "INVALID_ENDPOINT"),  # loopback http needs an explicit port
    ("https://exa mple.com", True, "INVALID_ENDPOINT"),
    ("", True, "INVALID_ENDPOINT"),
])
def test_destinations_refused(url, allowed, code):
    with pytest.raises(DestinationError) as ei:
        validate_provider_url(url, remote_allowed=allowed)
    assert ei.value.code == code


def test_config_requires_explicit_remote_permission_and_never_holds_the_key():
    ok = parse_config(b'[semantic]\nenabled = true\nprovider = "anthropic"\nmodel_id = "claude-sonnet-5-5"\napi_key_env = "ANTHROPIC_API_KEY"\n'
                      b'remote_allowed = true\n')
    s = ok.semantic
    assert s["base_url"] == "https://api.anthropic.com" and s["api"] == "anthropic_messages" and s["structured_output"] == "auto"
    bad = [
        b'[semantic]\nenabled = true\nprovider = "anthropic"\nmodel_id = "m"\napi_key_env = "K"\n',  # remote_allowed missing
        b'[semantic]\nenabled = true\nprovider = "anthropic"\nmodel_id = "m"\napi_key_env = "K"\nremote_allowed = false\n',
        b'[semantic]\nenabled = true\nprovider = "openai_compatible"\nbase_url = "http://api.example.com/v1"\nmodel_id = "m"\nremote_allowed = true\n',
        b'[semantic]\nenabled = true\nprovider = "anthropic"\nmodel_id = "m"\nremote_allowed = true\n',  # no api_key_env
        b'[semantic]\nprovider = "openai_compatible"\nbase_url = "https://x.example/v1"\napi_key = "sk-live-abc"\nremote_allowed = true\n',
        b'[semantic]\nprovider = "loopback"\nremote_allowed = true\n',
        b'[semantic]\nprovider = "openai_compatible"\nendpoint = "http://127.0.0.1:1"\n',
        b'[semantic]\nprovider = "anthropic"\nstructured_output = "json_object"\napi_key_env = "K"\nremote_allowed = true\n',
        b'[semantic]\nprovider = "cloud"\n',
    ]
    for text in bad:
        with pytest.raises(ConfigError):
            parse_config(text)
    # a pasted key in api_key_env is refused without echoing it
    with pytest.raises(ConfigError) as ei:
        parse_config(b'[semantic]\nprovider = "openai_compatible"\nbase_url = "http://127.0.0.1:9/v1"\napi_key_env = "sk-live-0123456789abcdef"\n')
    assert "sk-live" not in str(ei.value) and "NAME" in str(ei.value)
    with pytest.raises(ConfigError) as ei:
        parse_config(b'[semantic]\napi_key = "sk-live-0123456789abcdef"\n')
    assert "sk-live" not in str(ei.value) and "api_key_env" in str(ei.value)


def test_openai_success_structured_output_and_request_shape(key, loopback_only):
    good = {"candidates": []}
    with FakeProvider("openai", answer=lambda s, b: json.dumps(good)) as fp:
        r = OpenAICompatibleReasoner(validate_provider_url(fp.base_url, remote_allowed=False), "fake-remote-1", api_key_env=KEY_ENV, timeout_s=5)
        caps = r.capabilities()
        assert caps.available and caps.identity.artifact_id.startswith("openai_compatible:127.0.0.1/")
        resp = r.propose_contracts(_prompt())
    assert json.loads(resp.text) == good and resp.request_bytes and resp.request_sha256.startswith("sha256:")
    (post,) = fp.posts()
    assert post["path"] == "/v1/chat/completions" and post["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert post["body"]["response_format"]["type"] == "json_schema" and post["body"]["stream"] is False
    assert [m["role"] for m in post["body"]["messages"]] == ["system", "user"]
    assert all(a[0] == "127.0.0.1" for a in loopback_only)
    assert SECRET not in repr(r.__dict__) and SECRET not in json.dumps(caps.to_json())


def test_openai_auto_downgrades_to_json_mode_only_on_a_rejected_schema(key):
    with FakeProvider("openai", mode="reject_json_schema", answer=lambda s, b: '{"candidates": []}') as fp:
        r = OpenAICompatibleReasoner(validate_provider_url(fp.base_url, remote_allowed=False), "fake-remote-1", api_key_env=KEY_ENV, timeout_s=5)
        resp = r.propose_contracts(_prompt())
        assert resp.request_options["structured_output"] == "json_object" and resp.request_options["structured_output_downgraded_from_json_schema"]
        assert [p["body"]["response_format"]["type"] for p in fp.posts()] == ["json_schema", "json_object"]
        # an explicit json_schema setting never downgrades
        r2 = OpenAICompatibleReasoner(validate_provider_url(fp.base_url, remote_allowed=False), "fake-remote-1", api_key_env=KEY_ENV, timeout_s=5,
                                      structured_output="json_schema")
        with pytest.raises(ReasonerUnavailable) as ei:
            r2.propose_contracts(_prompt())
        assert ei.value.code == "BAD_REQUEST"


@pytest.mark.parametrize("mode,code", [("malformed", "MALFORMED_RESPONSE"), ("oversized", "RESPONSE_TOO_LARGE"), ("redirect", "REDIRECT_REJECTED"),
                                       ("unauthorized", "AUTH_FAILED"), ("wrong_model", "MODEL_MISMATCH")])
@pytest.mark.parametrize("dialect", ["openai", "anthropic"])
def test_failure_modes_are_explicit_and_never_leak_the_key(dialect, mode, code, key):
    with FakeProvider(dialect, mode=mode) as fp:
        dest = validate_provider_url(fp.base_url, remote_allowed=False)
        r = (OpenAICompatibleReasoner(dest, "fake-remote-1", api_key_env=KEY_ENV, timeout_s=5) if dialect == "openai"
             else AnthropicReasoner(dest, "fake-remote-1", api_key_env=KEY_ENV, timeout_s=5))
        if mode in ("redirect", "unauthorized"):
            caps = r.capabilities()
            assert not caps.available and caps.reason.startswith(code) and SECRET not in caps.reason
            assert not fp.posts()  # nothing (no context) was sent after the probe failed
            return
        with pytest.raises(ReasonerUnavailable) as ei:
            r.propose_contracts(_prompt())
    assert ei.value.code == code
    assert SECRET not in str(ei.value) and SECRET not in repr(ei.value)


def test_timeout_and_unreachable(key):
    with FakeProvider("openai", mode="slow", slow_s=2.0) as fp:
        r = OpenAICompatibleReasoner(validate_provider_url(fp.base_url, remote_allowed=False), "fake-remote-1", api_key_env=KEY_ENV, timeout_s=0.5)
        with pytest.raises(ReasonerUnavailable) as ei:
            r.propose_contracts(_prompt())
        assert ei.value.code == "TIMEOUT"
    port = unused_loopback_port()
    r = OpenAICompatibleReasoner(validate_provider_url(f"http://127.0.0.1:{port}/v1", remote_allowed=False), "m", timeout_s=2)
    caps = r.capabilities()
    assert not caps.available and caps.reason.startswith("UNREACHABLE")


def test_missing_key_is_reported_by_name_and_nothing_is_sent(monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    with FakeProvider("anthropic") as fp:
        r = AnthropicReasoner(validate_provider_url(fp.base_url, remote_allowed=False), "fake-remote-1", api_key_env=KEY_ENV, timeout_s=5)
        caps = r.capabilities()
        assert not caps.available and caps.reason.startswith("API_KEY_MISSING") and KEY_ENV in caps.reason
        assert fp.requests == []


def test_request_size_cap_sends_nothing(key):
    with FakeProvider("openai") as fp:
        r = OpenAICompatibleReasoner(validate_provider_url(fp.base_url, remote_allowed=False), "fake-remote-1", api_key_env=KEY_ENV, timeout_s=5,
                                     max_request_bytes=4096)
        p = _prompt()
        p.user += "x" * 8000
        with pytest.raises(ReasonerUnavailable) as ei:
            r.propose_contracts(p)
        assert ei.value.code == "REQUEST_TOO_LARGE" and fp.posts() == []


def test_echoed_key_is_redacted_from_parsed_text(key):
    with FakeProvider("openai", mode="echo_key", answer=lambda s, b: '{"candidates": [], "note": "ECHO"}') as fp:
        r = OpenAICompatibleReasoner(validate_provider_url(fp.base_url, remote_allowed=False), "fake-remote-1", api_key_env=KEY_ENV, timeout_s=5)
        resp = r.propose_contracts(_prompt())
    assert SECRET not in resp.text and "[REDACTED]" in resp.text


def test_anthropic_request_uses_unrolled_schema_and_reads_text_blocks(key):
    with FakeProvider("anthropic", answer=lambda s, b: '{"candidates": []}') as fp:
        r = AnthropicReasoner(validate_provider_url(fp.base_url, remote_allowed=False), "fake-remote-1", api_key_env=KEY_ENV, timeout_s=5,
                              effort="low")
        caps = r.capabilities()
        assert caps.available and caps.context_limit_tokens == 100000
        resp = r.propose_contracts(_prompt())
    assert resp.text == '{"candidates": []}' and resp.done_reason == "end_turn"
    (post,) = fp.posts()
    h = {k.lower(): v for k, v in post["headers"].items()}
    assert h["x-api-key"] == SECRET and h["anthropic-version"] == "2023-06-01" and "authorization" not in h
    b = post["body"]
    assert b["model"] == "fake-remote-1" and b["system"].startswith("S:") and b["messages"][0]["role"] == "user"
    assert "temperature" not in b  # not sent: current models reject non-default sampling parameters
    fmt = b["output_config"]["format"]
    assert fmt["type"] == "json_schema" and b["output_config"]["effort"] == "low"
    text = json.dumps(fmt["schema"])
    assert "maxItems" not in text and "minimum" not in text and len(text) < 60000
    assert _acyclic(fmt["schema"])


def _acyclic(schema) -> bool:
    import re

    defs = schema["$defs"]
    edges = {k: set(re.findall(r'"#/\$defs/([A-Za-z0-9_]+)"', json.dumps(v))) for k, v in defs.items()}
    seen, done = set(), set()

    def visit(n):
        if n in done:
            return True
        if n in seen:
            return False
        seen.add(n)
        ok = all(visit(m) for m in edges.get(n, ()))
        done.add(n)
        return ok

    return all(visit(k) for k in defs)


def test_unroll_schema_is_acyclic_linear_and_keeps_leaf_nodes():
    s = response_schema(("xs",), "bounded_builtin_int_list_v1", 2, 3)
    assert not _acyclic(s)  # the original is recursive
    u = unroll_schema(s, 2)
    assert _acyclic(u) and set(u["$defs"]) == {"pre_0", "pre_1", "pre_2", "post_0", "post_1", "post_2"}
    leaf = json.dumps(u["$defs"]["post_2"])
    assert "$ref" not in leaf and "result" in leaf and "repeatability" in leaf
    assert len(json.dumps(unroll_schema(s, 8))) < 4 * len(json.dumps(unroll_schema(s, 2)))


def test_make_remote_reasoner_rechecks_the_destination():
    with pytest.raises(ReasonerUnavailable) as ei:
        make_remote_reasoner({"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "model_id": "m", "remote_allowed": False})
    assert ei.value.code == "REMOTE_NOT_ALLOWED"
    with pytest.raises(ReasonerUnavailable) as ei:
        make_remote_reasoner({"provider": "openai_compatible", "base_url": "http://api.example.com/v1", "model_id": "m", "remote_allowed": True})
    assert ei.value.code == "INSECURE_REMOTE"


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl CLI not available to make a self-signed certificate")
def test_tls_verification_is_always_on(tmp_path, key):
    """An https endpoint with an untrusted (self-signed) certificate is refused; verification cannot be disabled."""
    cert, keyf = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(keyf), "-out", str(cert), "-days", "1",
                    "-subj", "/CN=localhost"], check=True, capture_output=True)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data": []}')

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(keyf))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        r = OpenAICompatibleReasoner(validate_provider_url(f"https://localhost:{srv.server_address[1]}/v1", remote_allowed=False), "m",
                                     api_key_env=KEY_ENV, timeout_s=5)
        caps = r.capabilities()
    finally:
        srv.shutdown()
        srv.server_close()
    assert not caps.available and caps.reason.startswith("TLS_ERROR")
    assert os.environ[KEY_ENV] == SECRET and SECRET not in caps.reason
