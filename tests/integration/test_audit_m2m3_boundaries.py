"""Audit (M2/M3) boundary tests: evaluator purity, untrusted runner output, static sentinel via verify.

* A3-01: runner output is untrusted (target code can forge its observation frame). A
  deeply nested JSON document used to escape ``_parse_response`` as an uncaught
  RecursionError, turning a hostile target into an engine crash instead of an UNKNOWN
  runner outcome; inside the harness a forged deep frame aborted the whole batch.
* A2-01 (evaluator half): witness validation must not mutate the witness it reports, and
  augmented assignment must use the in-place protocol shared by aliases.
* Static boundary for ``guard verify`` (quick and full) and ``guard contracts discover``:
  neither the base nor the candidate snapshot is ever imported or executed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from invariantguard.config import load_config
from invariantguard.execution.process import ProcessResult
from invariantguard.execution.runner import HARNESS_NAME, _parse_response, harness_bytes, validate_observations
from invariantguard.ir.evaluator import evaluate
from invariantguard.orchestration import SEMANTICS_PROFILE, lower_snapshot
from invariantguard.snapshots.capture import capture

from ..conftest import run_guard_subprocess
from .gitrepo import config, make_repo, write


def _proc(stdout: bytes) -> ProcessResult:
    return ProcessResult("runner", 0, stdout, b"", False, False, 1.0)


@pytest.mark.parametrize("depth", [5_000, 200_000])
def test_deeply_nested_runner_output_is_a_runner_error_not_a_crash(depth):
    deep = b'{"protocol":"ig-probe-1","mode":"probe","observations":' + b"[" * depth + b"]" * depth + b"}"
    resp, err = _parse_response(_proc(deep), "probe")  # must not raise RecursionError
    # Either rejected while parsing, or parsed and then rejected as not matching the probes.
    assert (resp is None and "nested too deeply" in err) or validate_observations(resp, ["p0"]) is not None


FORGER = '''
import os

def f(x):
    frame = b"[" * 200000 + b"]" * 200000
    frame = len(frame).to_bytes(8, "big") + frame
    for fd in range(3, 64):
        try:
            os.write(fd, frame)
        except OSError:
            pass
    os._exit(0)
'''


def test_harness_turns_a_forged_deep_frame_into_a_crashed_probe(tmp_path):
    """Run the shipped harness (unisolated, in a temp dir) on a target that forges its frame."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "forger.py").write_text(FORGER)
    harness = tmp_path / HARNESS_NAME
    harness.write_bytes(harness_bytes())
    req = {"mode": "probe", "src_root": str(src), "module": "forger", "function": "f", "positional": ["x"], "keyword_only": [],
           "python_path_root": ".", "probe_wall_seconds": 10, "probe_cpu_seconds": 10, "max_result_bytes": 1 << 20,
           "probes": [{"id": "p0", "args": {"x": {"t": "int", "v": "1"}}, "repeats": 1, "seed": 0}]}
    p = subprocess.run([sys.executable, "-I", "-S", "-B", str(harness)], input=json.dumps(req).encode(), capture_output=True,
                       timeout=60, cwd=tmp_path, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    resp = json.loads(p.stdout)
    assert resp["mode"] == "probe", resp  # not a whole-batch harness error
    assert [o["status"] for o in resp["observations"]] == ["crashed"]


def _lowered_fn(root: Path, symbol: str):
    cfg = load_config(root / ".guard" / "config.toml")
    snap = capture(root, [root], cfg.exclude, cfg.digest, SEMANTICS_PROFILE)
    lowered = lower_snapshot(snap, cfg)
    return next(f for m in lowered.modules for f in m.functions if f.symbol_id == symbol)


def test_evaluator_uses_inplace_protocol_and_never_mutates_the_witness(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "m.py").write_text("def f(xs):\n    ys = xs\n    ys += [1]\n    return 10 // len(xs)\n")
    (root / ".guard").mkdir()
    (root / ".guard" / "config.toml").write_text(
        'schema_version = "1.0"\n[[domains]]\nsymbol = "m.py::f"\nprofile = "python_exact_builtin_list"\nauthority = "DECLARED"\nreason = "t"\n')
    fn = _lowered_fn(root, "m.py::f")
    witness = {"xs": []}
    out = evaluate(fn, witness)
    assert witness == {"xs": []}, "the evaluator mutated the caller's witness"
    # CPython: ``ys += [1]`` extends the list xs also names, so len(xs) == 1 and f returns 10.
    assert out.kind == "RETURNED" and out.return_value == 10, (out.kind, out.detail)
    assert evaluate(fn, {}).kind == "STUCK"  # a missing argument stays UNMODELED after copying


SENTINEL_TEMPLATE = (
    "import pathlib\n"
    "pathlib.Path({marker!r}).write_text('executed')\n"
    "raise SystemExit('static analysis must never execute this module')\n\n\n"
    "def ratio(a, b):\n"
    "    \"\"\"\n    >>> ratio(3, 1)\n    50.0\n    \"\"\"\n"
    "    d = a - b\n"
    "{guard}"
    "    return 100 / d\n"
)


def test_verify_quick_full_and_discover_never_execute_either_snapshot(tmp_path):
    marker_base = tmp_path / "BASE_EXECUTED"
    marker_cand = tmp_path / "CANDIDATE_EXECUTED"
    base_src = SENTINEL_TEMPLATE.format(marker=str(marker_base), guard="    if d == 0:\n        return 0.0\n")
    root, base = make_repo(tmp_path, {"sent.py": base_src}, config("sent.py::ratio"))
    write(root, {"sent.py": SENTINEL_TEMPLATE.format(marker=str(marker_cand), guard="")})
    for mode in ("quick", "full"):
        p = run_guard_subprocess(["verify", str(root), "--base", base, "--mode", mode, "--format", "json", "--no-cache"], cwd=tmp_path)
        assert p.returncode == 1, p.stderr
        rep = json.loads(p.stdout)
        f = [x for x in rep["findings"] if x["symbol_id"] == "sent.py::ratio"]
        assert f and f[0]["patch_relation"] == "INTRODUCED" and f[0]["property_result"] == "PROVED_UNSAFE", rep["findings"]
    p = run_guard_subprocess(["contracts", "discover", "--root", str(root), "--dry-run"], cwd=root)
    assert p.returncode in (0, 2), (p.returncode, p.stderr)
    assert not marker_base.exists(), "the base snapshot was executed"
    assert not marker_cand.exists(), "the candidate snapshot was executed"


# -- A3-02: contract DSL Kleene logic with ill-typed operands --------------------------------------------
def _dsl_eval(node, result):
    from invariantguard.contracts.dsl import EvalEnv, evaluate as dsl_evaluate

    env = EvalEnv(args={}, result=result, has_result=True, before={}, after={}, before_enc={}, after_enc={}, repeat_encodings=[])
    return dsl_evaluate(node, env).status


_R = {"kind": "result"}
_LT5 = {"kind": "lt", "left": _R, "right": {"kind": "literal", "value": 5}}
_ISNONE = {"kind": "eq", "left": _R, "right": {"kind": "literal", "value": None}}


@pytest.mark.parametrize("node,expected", [
    # "result is None or result < 5": a None result satisfies the contract. Before the fix the
    # mismatch in lt(None, 5) escaped the `or` and the contract was reported violated, which
    # could turn a permitted None into a false OBSERVED_REGRESSION (BLOCK).
    ({"kind": "or", "args": [_ISNONE, _LT5]}, {None: "TRUE", 3: "TRUE", 7: "FALSE", True: "FALSE"}),
    ({"kind": "or", "args": [_LT5, _ISNONE]}, {None: "TRUE", 3: "TRUE", 7: "FALSE", True: "FALSE"}),
    # A decisive False settles `and` regardless of an ill-typed sibling.
    ({"kind": "and", "args": [_LT5, _ISNONE]}, {None: "FALSE", 3: "FALSE", 7: "FALSE"}),
    # Without a decisive sibling a mismatch is a violation, also under `not`.
    ({"kind": "not", "arg": _LT5}, {None: "FALSE", 3: "FALSE", 7: "TRUE", True: "FALSE"}),
])
def test_dsl_decisive_operand_wins_over_type_mismatch(node, expected):
    from invariantguard.contracts.dsl import check_expression

    check_expression(node, {}, "predicate", "predicate")  # these are valid contracts
    assert {r: _dsl_eval(node, r) for r in expected} == expected


def test_dsl_mismatch_with_undecided_sibling_is_unknown():
    # lt("a", 5) is ill-typed; the sibling needs argument snapshots that are unavailable, so it
    # could still decide the `or`: the honest answer is UNKNOWN (no evidence), never FALSE.
    undecided = {"kind": "unchanged_argument", "name": "xs"}
    assert _dsl_eval({"kind": "or", "args": [_LT5, undecided]}, "a") == "UNKNOWN"
    assert _dsl_eval({"kind": "or", "args": [_LT5, _ISNONE]}, "a") == "FALSE"  # both decided: violation


def test_permitted_none_result_is_not_a_contract_regression(tmp_path):
    """A3-02 end to end (unisolated backend, explicitly allowed): the contract admits None.

    The base returns a small int, the candidate returns None for odd inputs. Both satisfy
    ``result is None or result < 5``; before the fix the candidate was an OBSERVED_REGRESSION.
    """
    from .contract_helpers import contract_result, declare, exec_config, repo, verify_exec, write

    base_src = "def clamp(x):\n    return 1\n"
    cand_src = "def clamp(x):\n    if x % 2:\n        return None\n    return 1\n"
    root, base = repo(tmp_path, {"demo/m.py": base_src}, exec_config("unisolated", require_coverage=False))
    contract = {"contract_id": "clamp.small.v1", "scope": "demo/m.py::clamp", "precondition": {"kind": "literal", "value": True},
                "predicate": {"kind": "or", "args": [
                    {"kind": "eq", "left": {"kind": "result"}, "right": {"kind": "literal", "value": None}},
                    {"kind": "lt", "left": {"kind": "result"}, "right": {"kind": "literal", "value": 5}}]},
                "input_profile": "bounded_builtin_ints_v1", "examples": [{"x": 1}, {"x": 2}]}
    declare(root, contract, tmp_path)
    write(root, {"demo/m.py": cand_src})
    code, rep, err = verify_exec(root, base)
    r = contract_result(rep, "clamp.small.v1")
    assert r["property_result"] == "TESTED", r
    assert r["probes"]["candidate_fail"] == 0 and r["probes"]["candidate_pass"] > 0
    assert not [f for f in rep["findings"] if f.get("contract")]


def test_memory_error_under_the_runner_cap_is_unknown_not_a_violation():
    """A3-03: MemoryError inside the memory-capped runner is an environment outcome."""
    from invariantguard.contracts.checker import evaluate_observation
    from invariantguard.contracts.model import validate_revision  # noqa: F401  (module import check)

    class _TC:  # the minimal TypedContract surface evaluate_observation reads before the predicate
        repeat = None

    mem = {"status": "ok", "results": [{"kind": "raised", "exception": {"type": "MemoryError", "message": "", "frames": []}}]}
    other = {"status": "ok", "results": [{"kind": "raised", "exception": {"type": "ZeroDivisionError", "message": "division by zero", "frames": []}}]}
    assert evaluate_observation(_TC(), {}, mem, None)["verdict"] == "UNKNOWN"
    assert evaluate_observation(_TC(), {}, other, None)["verdict"] == "FAIL"  # a program exception still violates the contract
