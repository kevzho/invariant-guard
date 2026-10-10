"""Phase 2: replay harness v2 (ig-replay-1) through the runner.

Most cases run on the explicit unisolated backend (host interpreter, same harness bytes and
flags) to keep them fast; ``test_replay_in_docker`` runs the same harness in the real
container when Docker and the prepared image are available.
"""

from __future__ import annotations

import pytest

from invariantguard.execution.profiles import parse_profile
from invariantguard.execution.replay_harness import decode as h_decode
from invariantguard.execution.replay_harness import encode as h_encode
from invariantguard.execution.runner import DockerBackend, UnisolatedBackend
from invariantguard.replay.run import Construct, Expr, InputRejected, Namespace, Probe, Target, Value, run_replay, validate_observation
from invariantguard.replay.values import EncodeError, check_node, decode_input, encode_input

SRC = {
    "pkg/__init__.py": b"",
    "pkg/mod.py": b'''
import datetime, itertools, time

TABLE = {"k": 1}

def add(a, b=1, *rest, scale=1):
    return (a + b + sum(rest)) * scale

def chunks(xs, n):
    if n < 1:
        raise ValueError("n must be >= 1")
    for i in range(0, len(xs), n):
        yield xs[i:i + n]

def lazy_fail(xs):
    for x in xs:
        if x < 0:
            raise ValueError("negative")
        yield x

def forever():
    return itertools.count()

def when(y):
    return datetime.date(y, 1, 1), {"b", "a", "c"}, frozenset({3, 1}), b"\\x00\\xff", {1: "one"}

def append_one(xs):
    xs.append(1)
    return len(xs)

def spin():
    while True:
        pass

def sleepy():
    time.sleep(60)

def big(n):
    return "x" * n

class Box:
    LIMIT = 3

    def __init__(self, items=()):
        self.items = list(items)

    def push(self, x):
        self.items.append(x)
        return len(self.items)

    @property
    def size(self):
        return len(self.items)

    @staticmethod
    def make(n):
        return list(range(n))

    @classmethod
    def named(cls):
        return cls.__name__
''',
    "tests/test_mod.py": b"from pkg.mod import Box\n\ndef test_box():\n    assert Box([1, 2]).push(3) == 3\n",
}
PROV = dict(SRC)
M = "pkg/mod.py"


def T(q: str) -> Target:
    return Target(M, q)


@pytest.fixture(scope="module")
def unisolated():
    return UnisolatedBackend(parse_profile("u", {"backend": "unisolated", "allowed_targets": ["pkg/*.py::*"], "probe_wall_seconds": 1.0,
                                                 "wall_seconds": 60, "cpu_seconds": 5}))


def _run(b, probes, **kw):
    r = run_replay(b, SRC, probes, provenance=PROV, **kw)
    return r, r.observations


def _ret(o):
    assert o["status"] == "ok", o
    assert o["result"]["kind"] == "returned", o
    return o["result"]["value"]


def test_functions_args_kwargs_and_varargs(unisolated):
    _, obs = _run(unisolated, [
        Probe("p1", T("add"), (Value.of(1), Value.of(2), Value.of(3), Value.of(4)), (("scale", Value.of(10)),)),
        Probe("p2", T("add"), (Value.of("a"),)),
    ])
    assert decode_input(_ret(obs["p1"])) == 100
    assert obs["p2"]["result"] == {"kind": "raised", "exception": {"type": "TypeError", "message": 'can only concatenate str (not "int") to str'}}


def test_generators_are_materialized_with_lazy_exceptions_and_prefix(unisolated):
    _, obs = _run(unisolated, [
        Probe("g", T("chunks"), (Value.of([1, 2, 3, 4, 5]), Value.of(2))),
        Probe("g0", T("chunks"), (Value.of([1]), Value.of(0))),
        Probe("lazy", T("lazy_fail"), (Value.of([1, -1, 2]),)),
        Probe("inf", T("forever")),
    ], iter_prefix=5)
    g = _ret(obs["g"])
    assert g["t"] == "iter" and [decode_input(x) for x in g["v"]] == [[1, 2], [3, 4], [5]] and g["truncated"] is False
    g0 = _ret(obs["g0"])  # a generator raises lazily, on first next()
    assert g0["v"] == [] and g0["raised"] == {"type": "ValueError", "message": "n must be >= 1"}
    lazy = _ret(obs["lazy"])
    assert [decode_input(x) for x in lazy["v"]] == [1] and lazy["raised"]["message"] == "negative"
    inf = _ret(obs["inf"])
    assert len(inf["v"]) == 5 and inf["truncated"] is True


def test_rich_values_compare_by_content(unisolated):
    _, obs = _run(unisolated, [Probe("a", T("when"), (Value.of(2024),)), Probe("b", T("when"), (Value.of(2025),))])
    a, b = _ret(obs["a"]), _ret(obs["b"])
    assert a != b  # dates differed but used to encode as the same 'opaque'
    date, s, fs, by, xd = a["v"]
    assert date == {"t": "dt", "type": "date", "v": "2024-01-01"}
    assert [x["v"] for x in s["v"]] == ["a", "b", "c"] and s["t"] == "set"
    assert fs["t"] == "frozenset" and by == {"t": "bytes", "v": "00ff"} and xd["t"] == "xdict"


def test_mutated_arguments_are_reported(unisolated):
    _, obs = _run(unisolated, [Probe("m", T("append_one"), (Value.of([7]),))])
    assert decode_input(obs["m"]["mutated"]["arg0"]) == [7, 1]


def test_methods_properties_static_class_methods_and_constructors(unisolated):
    _, obs = _run(unisolated, [
        Probe("push", T("Box.push"), (Value.of(9),), receiver=Construct((Value.of([1]),))),
        Probe("size", T("Box.size"), receiver=Construct((Value.of([1, 2]),))),
        Probe("static", T("Box.make"), (Value.of(3),)),
        Probe("cls", T("Box.named")),
        Probe("ctor", T("Box"), (Value.of([4]),)),
        Probe("norecv", T("Box.push"), (Value.of(1),)),
    ])
    assert decode_input(_ret(obs["push"])) == 2
    assert obs["push"]["receiver_after"]["state"] == [["items", encode_input([1, 9])]]
    assert decode_input(_ret(obs["size"])) == 2
    assert decode_input(_ret(obs["static"])) == [0, 1, 2]
    assert decode_input(_ret(obs["cls"])) == "Box"
    ctor = _ret(obs["ctor"])
    assert ctor["t"] == "object" and ctor["type"] == "pkg.mod.Box" and ctor["state"] == [["items", encode_input([4])]]
    assert obs["norecv"]["status"] == "input_error"


def test_repository_expressions_from_tests(unisolated):
    ns = Namespace(imports=(("Box", "pkg.mod", "Box"),))
    _, obs = _run(unisolated, [Probe("x", T("Box.push"), (Expr("3", "tests/test_mod.py", 4, ns),), receiver=Expr("Box([1, 2])", "tests/test_mod.py", 4, ns))])
    assert decode_input(_ret(obs["x"])) == 3


def test_expressions_must_occur_in_the_repository(unisolated):
    ns = Namespace(imports=(("Box", "pkg.mod", "Box"),))
    with pytest.raises(InputRejected, match="does not occur"):
        _run(unisolated, [Probe("x", T("Box.make"), (Expr("__import__('os').system('true')", "tests/test_mod.py", 1, ns),))])
    with pytest.raises(InputRejected, match="not in the snapshot"):
        _run(unisolated, [Probe("x", T("Box.make"), (Expr("3", "elsewhere.py", 1, ns),))])


def test_targets_outside_the_allowlist_are_not_run():
    b = UnisolatedBackend(parse_profile("u", {"backend": "unisolated", "allowed_targets": ["pkg/mod.py::add"]}))
    _, obs = _run(b, [Probe("ok", T("add"), (Value.of(1),)), Probe("no", T("big"), (Value.of(1),))])
    assert obs["ok"]["status"] == "ok" and obs["no"]["status"] == "not_allowed"


def test_timeouts_are_attributed_to_one_probe_and_others_still_run(unisolated):
    _, obs = _run(unisolated, [Probe("a", T("add"), (Value.of(1),)), Probe("spin", T("spin")), Probe("sleep", T("sleepy")),
                               Probe("b", T("add"), (Value.of(2),))])
    assert obs["spin"]["status"] == "timeout" and obs["sleep"]["status"] == "timeout"
    assert decode_input(_ret(obs["a"])) == 2 and decode_input(_ret(obs["b"])) == 3


def test_large_values_are_replaced_by_a_digest(unisolated):
    _, obs = _run(unisolated, [Probe("big", T("big"), (Value.of(5000),)), Probe("big2", T("big"), (Value.of(5001),))], max_observation_bytes=1024)
    a, b = _ret(obs["big"]), _ret(obs["big2"])
    assert a["t"] == "digest" and b["t"] == "digest" and a["sha256"] != b["sha256"]


def test_import_errors_are_per_module(unisolated):
    files = {**SRC, "pkg/broken.py": b"raise RuntimeError('boom at import')\n"}
    r = run_replay(unisolated, files, [Probe("x", Target("pkg/broken.py", "f")), Probe("y", T("add"), (Value.of(1),))], provenance=files)
    assert r.observations["x"]["status"] == "import_error" and "boom" in r.observations["x"]["detail"]["message"]
    assert r.observations["y"]["status"] == "ok"


def test_string_hashing_is_deterministic_across_runs(unisolated):
    files = {"h.py": b"def h():\n    return list({'alpha', 'beta', 'gamma', 'delta', 'eps'})\n"}
    b = UnisolatedBackend(parse_profile("u", {"backend": "unisolated", "allowed_targets": ["*::*"]}))
    first, second = (run_replay(b, files, [Probe("p", Target("h.py", "h"))], provenance=files).observations["p"] for _ in range(2))
    assert first["status"] == "ok" and first["result"] == second["result"]


def test_identity_and_digests_are_recorded(unisolated):
    r, _ = _run(unisolated, [Probe("a", T("add"), (Value.of(1),))])
    meta = r.to_json()
    assert meta["harness"] == "ig-replay-1" and meta["harness_digest"].startswith("sha256:")
    assert r.identity["hash_seed"] == "0" and r.identity["hash_randomization"] == 0


# -- untrusted observations and host/harness encoding agreement -----------------------------------------
@pytest.mark.parametrize("value", [None, True, 0, -5, 2 ** 70, 1.5, float("inf"), "s", [1, (2, "x")], {"a": [1]}, {1: 2, (1, 2): None},
                                   {3, 1, 2}, frozenset({"b", "a"}), b"\x00", bytearray(b"ab"), 1 + 2j, range(1, 9, 2)])
def test_host_and_harness_encodings_agree(value):
    assert encode_input(value) == h_encode(value)
    assert decode_input(encode_input(value)) == value == h_decode(h_encode(value))


def test_host_refuses_non_builtin_inputs():
    class C:
        pass

    with pytest.raises(EncodeError):
        encode_input(C())
    with pytest.raises(EncodeError):
        encode_input([1, {2: C()}])


@pytest.mark.parametrize("obs", [
    {"status": "pwned"},
    {"status": "ok"},
    {"status": "ok", "result": {"kind": "returned", "value": {"t": "evil"}}},
    {"status": "ok", "result": {"kind": "raised", "exception": {"type": 1}}},
    {"status": "ok", "result": {"kind": "returned", "value": {"t": "none"}}, "mutated": {"a": {"t": "??"}}},
    "not a dict",
])
def test_malformed_observations_are_invalid_not_trusted(obs):
    assert validate_observation(obs)["status"] == "invalid_observation"


def test_deeply_nested_observation_is_rejected():
    node = {"t": "none"}
    for _ in range(200):
        node = {"t": "list", "v": [node]}
    assert check_node(node) is not None


# -- real container ---------------------------------------------------------------------------------------
def test_replay_in_docker():
    b = DockerBackend(parse_profile("d", {"backend": "docker", "image": "python:3.13-slim", "allowed_targets": ["pkg/*.py::*"],
                                          "probe_wall_seconds": 1.0, "memory_mb": 256, "max_processes": 32}))
    if not b.status().available:
        pytest.skip(f"docker backend unavailable here: {b.status().reason}")
    r = run_replay(b, SRC, [Probe("g", T("chunks"), (Value.of([1, 2, 3]), Value.of(2))), Probe("spin", T("spin")),
                            Probe("push", T("Box.push"), (Value.of(9),), receiver=Construct((Value.of([1]),)))], provenance=PROV)
    assert [decode_input(x) for x in _ret(r.observations["g"])["v"]] == [[1, 2], [3]]
    assert r.observations["spin"]["status"] == "timeout"
    assert decode_input(_ret(r.observations["push"])) == 2
    assert r.identity["hash_randomization"] == 0 and r.environment_digest.startswith("sha256:")


def test_site_packages_are_opt_in_and_identified_in_docker():
    """dateutil needs six at import time (spike gap 4). Uses an image with extra packages when present."""
    files = {"d.py": b"import six\n\ndef f():\n    return six.PY3\n"}
    base = {"backend": "docker", "image": "invariantguard-diffreplay:spike", "allowed_targets": ["*::*"], "memory_mb": 256, "max_processes": 32}
    off = DockerBackend(parse_profile("off", dict(base)))
    if not off.status().available:
        pytest.skip(f"image with extra packages unavailable here: {off.status().reason}")
    on = DockerBackend(parse_profile("on", {**base, "site_packages": True}))
    r_off = run_replay(off, files, [Probe("p", Target("d.py", "f"))], provenance=files)
    r_on = run_replay(on, files, [Probe("p", Target("d.py", "f"))], provenance=files)
    assert r_off.observations["p"]["status"] == "import_error" and "six" in r_off.observations["p"]["detail"]["message"]
    assert decode_input(_ret(r_on.observations["p"])) is True
    assert "six==1.17.0" in r_on.identity["distributions"]
    assert r_on.environment_digest != r_off.environment_digest and r_on.environment_digest.startswith("sha256:")
