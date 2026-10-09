"""Real container runs (M3 T34/T35): isolation claims match observed behavior; Fixture B/E through Docker.

Requires a running Docker daemon and the image prepared by `guard runner setup` (python:3.13-slim).
When either is missing every test here is skipped with the reason, and the fail-closed behavior
is covered by ``test_execution_failclosed.py`` instead (no unisolated fallback).
"""

import json
import os
import math

import pytest

from invariantguard.contracts.values import decode, encode
from invariantguard.execution.profiles import parse_profile
from invariantguard.execution.runner import DockerBackend, Workspace

from .contract_helpers import (
    NORMALIZE_BASE,
    NORMALIZE_PATCH,
    SORTED_BASE,
    SORTED_PATCH,
    XS,
    contract_result,
    declare,
    exec_config,
    normalize_contract,
    repo,
    side_evidence,
    probe_with_input,
    unchanged_contract,
    verify_exec,
    write,
)

PROFILE = {"backend": "docker", "image": "python:3.13-slim", "allowed_targets": ["*::*"], "probe_wall_seconds": 2.0, "memory_mb": 128,
           "max_processes": 16}


@pytest.fixture(scope="module")
def backend():
    b = DockerBackend(parse_profile("iso", dict(PROFILE)))
    st = b.status()
    if not st.available:
        pytest.skip(f"docker backend unavailable here: {st.reason}")
    return b


def _probe(b, src: str, fn: str, args: dict, *, params=("x",)) -> dict:
    ws = Workspace({"t.py": src.encode()}, "itest")
    try:
        req = b.probe_request(ws, {"module": "t", "function": fn, "positional": list(params), "keyword_only": [], "python_path_root": ".",
                                   "probes": [{"id": "p1", "args": {k: encode(v) for k, v in args.items()}, "repeats": 1, "seed": 0}]})
        run = b.run(ws, req)
        assert run.response is not None, run.error
        (obs,) = run.response["observations"]
        return obs
    finally:
        ws.close()


def _value(obs):
    assert obs["status"] == "ok", obs
    (res,) = obs["results"]
    assert res["kind"] == "returned", res
    return decode(res["value"])


def test_every_claimed_capability_is_verified_from_inside(backend):
    st = backend.status()
    caps = {c.name: c for c in st.capabilities}
    assert set(caps) == {"deny_network", "readonly_source", "no_host_credentials", "scratch_only_writes", "resource_limits", "interpreter_minor_matches_host"}
    assert all(c.status == "VERIFIED" for c in caps.values())
    assert caps["deny_network"].detail["interfaces_up"] == [] and caps["deny_network"].detail["connect_test"].startswith("refused")
    assert caps["no_host_credentials"].detail["uid"] != 0 and caps["no_host_credentials"].detail["effective_capabilities"] == "0000000000000000"
    assert st.environment["image_id"].startswith("sha256:") and st.environment_digest.startswith("sha256:")


NET = '''
import errno, socket
def attempt(x):
    out = []
    try:
        socket.create_connection(("192.0.2.1", 80), timeout=1)
        out.append("tcp-connected")
    except OSError as e:
        out.append("tcp:" + str(errno.errorcode.get(e.errno, e.errno)))
    try:
        socket.getaddrinfo("example.com", 443)
        out.append("dns-resolved")
    except OSError as e:
        out.append("dns:" + type(e).__name__)
    return out
'''


def test_network_is_denied_inside_a_probe(backend):
    out = _value(_probe(backend, NET, "attempt", {"x": 0}))
    assert out[0] == "tcp:ENETUNREACH" and out[1].startswith("dns:") and "dns-resolved" not in out


def test_host_writes_are_denied(backend, tmp_path):
    marker = tmp_path / "host-marker.txt"
    src = f'''
import os
def attempt(x):
    out = {{}}
    for p in ("/work/src/injected.py", "/work/harness/probe_harness.py", "/etc/passwd", "/injected", {str(marker)!r}, {str(tmp_path)!r} + "/x"):
        try:
            with open(p, "a") as fh:
                fh.write("pwned")
            out[p] = "written"
        except OSError as e:
            out[p] = type(e).__name__
    try:
        open("/tmp/scratch.txt", "w").write("ok")
        out["/tmp"] = "written"
    except OSError as e:
        out["/tmp"] = type(e).__name__
    return out
'''
    out = _value(_probe(backend, src, "attempt", {"x": 0}))
    assert out.pop("/tmp") == "written"  # the tmpfs scratch is the only writable place
    assert all(v != "written" for v in out.values()), out
    assert not marker.exists() and not (tmp_path / "x").exists()


def test_environment_secrets_are_absent(backend, monkeypatch):
    monkeypatch.setenv("IG_TEST_SECRET", "s3cr3t-value")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-but-secret")
    src = '''
import os
def attempt(x):
    return sorted(os.environ.items())
'''
    env = dict(_value(_probe(backend, src, "attempt", {"x": 0})))
    assert "IG_TEST_SECRET" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert all("s3cr3t" not in v for v in env.values())
    assert env["HOME"] == "/tmp" and env["PYTHONHASHSEED"] == "0" and os.environ["HOME"] != "/tmp"


def test_resource_limits_and_timeouts_are_runner_outcomes(backend):
    mem = _probe(backend, "def attempt(x):\n    b = bytearray(512 * 1024 * 1024)\n    return len(b)\n", "attempt", {"x": 0})
    assert mem["status"] in ("crashed", "ok") and (mem["status"] == "crashed" or mem["results"][0]["kind"] == "raised")  # 128 MiB limit
    forks = _probe(backend, "import os\ndef attempt(x):\n    n = 0\n    try:\n        for _ in range(64):\n            if os.fork() == 0:\n                import time; time.sleep(5); os._exit(0)\n            n += 1\n    except OSError as e:\n        return [n, type(e).__name__]\n    return [n, 'no-limit']\n",
                   "attempt", {"x": 0})
    n, why = _value(forks)
    assert why != "no-limit" and n < 16  # pids limit
    hang = _probe(backend, "def attempt(x):\n    while True:\n        pass\n", "attempt", {"x": 0})
    assert hang["status"] == "timeout"


def test_no_leftover_containers(backend):
    r = backend.cli(["ps", "-a", "--filter", "name=ig-probe-", "--format", "{{.Names}}"])
    assert r.returncode == 0 and r.stdout.strip() == b""


# -- end to end through the CLI with the docker profile --------------------------------------------------
def test_fixture_b_through_containers(tmp_path, backend):
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, exec_config("docker"))
    declare(root, normalize_contract(), tmp_path)
    zero_only = {"kind": "and", "args": [{"kind": "gt", "left": {"kind": "len", "value": XS}, "right": {"kind": "literal", "value": 0}},
                                         {"kind": "all_between", "value": XS, "lower": 0.0, "upper": 0.0}]}
    declare(root, normalize_contract("normalize.zero.v1", precondition=zero_only, examples=[{"xs": [0.0, 0.0]}]), tmp_path)
    length = {"contract_id": "normalize.len.v1", "scope": "demo/math_ops.py::normalize", "precondition": normalize_contract()["precondition"],
              "predicate": {"kind": "eq", "left": {"kind": "len", "value": {"kind": "result"}}, "right": {"kind": "len", "value": XS}},
              "input_profile": "bounded_builtin_numeric_list_v1"}
    declare(root, length, tmp_path)
    write(root, {"demo/math_ops.py": NORMALIZE_PATCH})
    code, r, _ = verify_exec(root, base, profile="iso")
    assert r["capabilities"]["execution"] == "isolated" and r["contracts"]["execution"]["status"] == "AVAILABLE"
    assert all(c["status"] == "VERIFIED" for c in r["contracts"]["execution"]["capabilities"])
    c = contract_result(r, "normalize.sum.v1")
    assert (c["property_result"], c["patch_relation"], c["severity"]) == ("OBSERVED_REGRESSION", "INTRODUCED", "ERROR")
    assert c["original_witness"] == {"xs": [1.0, 1.0]}
    enc = {"xs": encode([1.0, 1.0])}
    assert probe_with_input(side_evidence(r, c, "base"), enc)["result"] == [0.5, 0.5]
    assert all(math.isclose(v, 1 / 3) for v in probe_with_input(side_evidence(r, c, "candidate"), enc)["result"])
    assert {"input": {"xs": [0.0, 0.0]}, "origin": "example", "why": "precondition is false"} in c["excluded_examples"]
    ev = side_evidence(r, c, "candidate")
    assert ev["execution"]["backend"] == "docker" and ev["execution"]["isolated"] is True
    assert ev["execution"]["environment"]["image_id"].startswith("sha256:") and ev["execution"]["interpreter"]["platform"] == "linux"
    z = contract_result(r, "normalize.zero.v1")
    assert (z["property_result"], z["patch_relation"], z["severity"]) == ("OBSERVED_FAILURE", "PRE_EXISTING", "WARNING")
    ln = contract_result(r, "normalize.len.v1")
    assert ln["property_result"] == "TESTED" and ln["finding_id"] is None
    assert code == 1 and r["verdict"] == "BLOCK"


def test_fixture_e_through_containers(tmp_path, backend):
    root, base = repo(tmp_path, {"demo/listy.py": SORTED_BASE}, exec_config("docker"))
    declare(root, unchanged_contract(), tmp_path)
    write(root, {"demo/listy.py": SORTED_PATCH})
    code, r, _ = verify_exec(root, base, profile="iso")
    c = contract_result(r, "sorted_copy.pure.v1")
    assert (c["property_result"], c["patch_relation"]) == ("OBSERVED_REGRESSION", "INTRODUCED") and code == 1
    assert c["original_witness"] == {"xs": [2, 1]} and c["observations"]["candidate"]["arguments_after_call"]["xs"] == sorted(c["witness"]["xs"])
