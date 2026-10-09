"""Deterministic contract templates with provenance (M3 T32). Discovery reads source only and never approves."""

import json

from invariantguard.models.schema import validation_errors

from ..conftest import run_cli
from .contract_helpers import contracts_cli, exec_config, repo

LIB = '''\
import random


def sorted_copy(xs):
    """Return a sorted copy.

    >>> sorted_copy([2, 1])
    [1, 2]
    """
    return sorted(xs)


def sort_in_place(xs):
    """>>> sort_in_place([3, 1])"""
    xs.sort()
    return xs


def normalize(xs: list[float]):
    return [x / sum(xs) for x in xs]


def clamp(x: int):
    assert x >= 0
    y = x + 1
    assert y > 0
    return y


def jitter(x: float):
    return x + random.random()


def opaque(thing):
    return thing
'''

SENTINEL = 'open("SENTINEL_TOUCHED", "w").write("x")\n\n\ndef f(xs: list[int]):\n    return xs\n'


def _discover(root, *extra):
    code, out, err = run_cli(["contracts", "discover", "--path", str(root), *extra])
    assert code == 0, err
    return json.loads(out)


def test_templates_propose_inferred_candidates_with_provenance(tmp_path):
    root, _ = repo(tmp_path, {"demo/lib.py": LIB, "demo/side_effect.py": SENTINEL}, exec_config())
    out = _discover(root)
    by = {c["contract_id"]: c for c in out["candidates"]}
    pure = by["demo.lib.sorted_copy.purity.xs"]
    assert pure["status"] == "proposed" and pure["input_profile"] == "bounded_builtin_int_list_v1"  # profile from the doctest literal
    assert pure["examples"] == [{"xs": [2, 1]}]
    assert {p["kind"] for p in pure["provenance"]} == {"template", "docstring"}
    assert any("inferred from doctest examples" in a for a in pure["assumptions"]) and any("INFERRED" in a for a in pure["assumptions"])
    assert pure["source_span"]["path"] == "demo/lib.py" and pure["source_span"]["start_line"] == 4
    # A function that mutates its argument gets no purity candidate; the reason is reported.
    assert "demo.lib.sort_in_place.purity.xs" not in by
    assert any(s.get("symbol_id") == "demo/lib.py::sort_in_place" and "sort" in s["reason"] for s in out["skipped"])
    assert by["demo.lib.normalize.length.xs"]["status"] == "proposed"
    assert by["demo.lib.normalize.length.xs"]["input_profile"] == "bounded_builtin_numeric_list_v1"
    # Entry assertions become the precondition; an assertion on the returned value becomes a predicate.
    clamp = next(c for cid, c in by.items() if cid.startswith("demo.lib.clamp.assertion"))
    assert clamp["status"] == "proposed" and {p["kind"] for p in clamp["provenance"]} == {"assertion"}
    # Nondeterministic names: no repeatability candidate; unknown domains are skipped, not guessed.
    assert "demo.lib.jitter.repeatability" not in by and "demo.lib.clamp.repeatability" in by
    assert any(s.get("symbol_id") == "demo/lib.py::opaque" and "no single input profile" in s["reason"] for s in out["skipped"])
    # Discovery parsed the module without executing it.
    assert not (root / "SENTINEL_TOUCHED").exists()
    assert validation_errors("candidate_batch", out["candidate_batch"]) == []
    # Nothing was approved.
    _, lst, _ = contracts_cli(root, "list")
    assert lst["contracts"] and {c["authority"] for c in lst["contracts"]} == {"INFERRED"}
    assert all(c["history"][0]["kind"] == "propose" for c in lst["contracts"])


def test_discovery_is_deterministic_and_idempotent(tmp_path):
    root, _ = repo(tmp_path, {"demo/lib.py": LIB}, exec_config())
    a = _discover(root, "--dry-run")
    b = _discover(root, "--dry-run")
    strip = lambda o: [{k: v for k, v in c.items() if k != "status"} for c in o["candidates"]]  # noqa: E731
    assert strip(a) == strip(b) and a["candidate_batch"] == b["candidate_batch"]
    assert {c["status"] for c in a["candidates"]} <= {"would_propose", "rejected"}
    assert not (root / ".guard" / "contracts").exists()  # dry run records nothing
    first = _discover(root)
    second = _discover(root)
    assert {c["status"] for c in second["candidates"] if c["status"] != "rejected"} == {"exists"}
    assert [c["semantic_digest"] for c in first["candidates"]] == [c["semantic_digest"] for c in second["candidates"]]


def test_model_discovery_requires_explicit_semantic_config(tmp_path):
    # M3 pinned "--model local is not implemented (exit 4)". M4 implements it (tests/integration/test_model_discovery.py);
    # without [semantic] enabled it is still a usage error that contacts nothing and prints no JSON.
    root, _ = repo(tmp_path, {"demo/lib.py": LIB}, exec_config())
    code, out, err = run_cli(["contracts", "discover", "--model", "local", "--path", str(root)])
    assert code == 4 and "[semantic] enabled = true" in err and out == ""
