"""Regression and adversarial tests added by the M0/M1 audit (docs/IMPLEMENTATION_STATUS.md, "Audit").

Each test names the audit finding it pins (A-xx). Expected results are derived from
Python semantics; failing witnesses are replayed in an isolated interpreter.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from invariantguard.analysis.intervals import AbsInt, Interval
from invariantguard.models.schema import validation_errors

from ..conftest import REPO, check_json, result_for, run_guard_subprocess, write_project


def _replay(path: Path, func: str, args: dict, optimize: bool = False) -> str:
    code = (
        "import runpy, sys, json\n"
        "ns = runpy.run_path(sys.argv[1])\n"
        "try:\n    ns[sys.argv[2]](**json.loads(sys.argv[3])); print('OK')\n"
        "except Exception as e:\n    print(type(e).__name__)\n"
    )
    cmd = [sys.executable, "-I"] + (["-O"] if optimize else []) + ["-c", code, str(path), func, json.dumps(args)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()


def _proj(tmp_path: Path, src: str, params: list[str] | None = None, *, declare: bool = True, extra: str = "") -> Path:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    cfg = 'schema_version = "1.0"\n[analysis]\nquick_budget_ms = 20000\n' + extra
    if declare:
        cfg += '[[domains]]\nsymbol = "m.py::f"\nprofile = "python_exact_builtin_ints"\nauthority = "DECLARED"\nreason = "audit fixture"\n'
        if params is not None:
            cfg += "params = " + json.dumps(params) + "\n"
    return write_project(root, {"m.py": src}, cfg)


# -- A-01: solver constants must not alias Python names --------------------------------
def test_a01_parameter_named_like_internal_solver_constant(tmp_path):
    src = "def f(floordiv_q_v3, b):\n    q = b // 2\n    return 1 / (q - floordiv_q_v3 + 1)\n"
    root = _proj(tmp_path, src)
    _, report, _ = check_json(root)
    r = result_for(report, "m.py::f", 1)
    # Before the fix the parameter aliased the internal quotient constant and this was PROVED_SAFE.
    assert r["property_result"] == "PROVED_UNSAFE", r["reasons"]
    assert _replay(root / "m.py", "f", r["witness"]) == "ZeroDivisionError"


# -- A-02: arithmetic on huge constants must terminate ----------------------------------
def test_a02_repeated_squaring_terminates_and_stays_sound(tmp_path):
    src = "def f():\n    x = 10\n" + "    x = x * x\n" * 40 + "    return 1 / x\n"
    root = _proj(tmp_path, src, declare=False)
    t0 = time.monotonic()
    p = run_guard_subprocess(["check", str(root), "--format", "json"])
    assert time.monotonic() - t0 < 60
    r = result_for(json.loads(p.stdout), "m.py::f")
    assert r["property_result"] == "PROVED_SAFE"  # 10**(2**40) is nonzero


def test_a02_interval_clamping_only_widens():
    big = 10**100
    c = AbsInt.const(big)
    assert c.excludes_zero() and c.itv.contains(big) and not c.itv.contains(0)
    n = AbsInt.const(-big)
    assert n.excludes_zero() and n.itv.contains(-big) and not n.itv.contains(0)
    prod = c.mul(c)
    assert prod.excludes_zero() and prod.itv.contains(big * big)
    assert Interval(-big, big).is_top()


# -- A-03: UnboundLocalError happens at a read, not at a join ----------------------------
def test_a03_unread_maybe_unbound_local_does_not_block_a_witness(tmp_path):
    src = "def f(a):\n    if a > 0:\n        d = 1\n    return 1 / (a - 2)\n"
    root = _proj(tmp_path, src)
    _, report, _ = check_json(root)
    r = result_for(report, "m.py::f")
    assert r["property_result"] == "PROVED_UNSAFE" and r["witness"] == {"a": 2}
    assert _replay(root / "m.py", "f", r["witness"]) == "ZeroDivisionError"


@pytest.mark.parametrize("no_solver", [False, True])
def test_a03_alias_read_of_unbound_local_raises_at_the_read(tmp_path, no_solver):
    # For a <= 0, `x = d` raises UnboundLocalError before the division; a witness must avoid it.
    src = "def f(a, b):\n    if a > 0:\n        d = 1\n    if b > 0:\n        d = 2\n    x = d\n    return 1 / (a + 3)\n"
    root = _proj(tmp_path, src)
    _, report, _ = check_json(root, *(["--no-solver"] if no_solver else []))
    r = result_for(report, "m.py::f")
    if r["property_result"] == "PROVED_UNSAFE":
        assert _replay(root / "m.py", "f", r["witness"]) == "ZeroDivisionError"
    else:
        assert r["property_result"] in ("UNKNOWN", "POSSIBLY_UNSAFE")
    if not no_solver:
        # a = -3 requires b > 0 so that d is bound: the solver encodes boundness exactly.
        assert r["property_result"] == "PROVED_UNSAFE" and r["witness"]["b"] > 0


def test_a03_ir_evaluator_raises_unbound_only_on_read():
    from invariantguard.frontends.python import lower_module, parse_source
    from invariantguard.ir.evaluator import evaluate
    from types import SimpleNamespace as NS

    src = b"def f(a: int):\n    if a > 0:\n        d = 1\n    return a\n\ndef g(a: int):\n    if a > 0:\n        d = 1\n    x = d\n    return a\n"
    parsed = parse_source("m.py", src)
    fr = lower_module(parsed, "sha256:" + "0" * 64, {})
    fns = {fn.name: fn for fn in fr.module.functions}
    assert evaluate(fns["f"], {"a": 0}).kind == "RETURNED"
    out = evaluate(fns["g"], {"a": 0})
    assert out.kind == "RAISED_OTHER" and "UnboundLocalError" in out.detail
    assert evaluate(fns["g"], {"a": 1}).kind == "RETURNED"
    del NS


# -- A-04 / A-05: in-scope sources that are not analyzed are required coverage ------------
def test_a04_explicit_non_python_target_is_incomplete(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "notes.txt").write_text("1 / 0\n")
    code, report, _ = check_json(root / "notes.txt")
    assert (code, report["verdict"], report["run_outcome"]) == (2, "INCOMPLETE", "INCOMPLETE")
    assert [s["kind"] for s in report["coverage"]["skipped_checks"]] == ["excluded_path"]


def test_a04_oversized_unreadable_and_symlinked_sources_are_required_skips(tmp_path, monkeypatch):
    from invariantguard.snapshots import capture as cap

    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "ok.py").write_text("def g():\n    return 1\n")
    (root / "big.py").write_text("def h():\n    return 1 / 0\n" + "#" * 200)
    (root / "alias.py").symlink_to(root / "ok.py")
    monkeypatch.setattr(cap, "MAX_FILE_BYTES", 100)
    code, report, _ = check_json(root)
    kinds = {(s["kind"], s["path"], s["required"]) for s in report["coverage"]["skipped_checks"]}
    assert kinds == {("excluded_path", "big.py", True), ("excluded_path", "alias.py", True)}
    assert (code, report["verdict"]) == (2, "INCOMPLETE")


def test_a04_non_required_skip_is_a_warning_not_a_pass(tmp_path):
    root = _proj(tmp_path, "def f():\n    return 1\n", declare=False, extra="require_coverage = false\n")
    (root / "broken.py").write_text("def f(:\n")
    code, report, _ = check_json(root)
    assert (code, report["verdict"], report["run_outcome"]) == (0, "WARN", "COMPLETE")


def test_a04_empty_scope_pass_is_labelled_vacuous(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()
    code, report, _ = check_json(root)
    assert code == 0 and report["verdict"] == "PASS"
    assert any("nothing was analyzed" in r for r in report["verdict_reasons"])


def test_a05_path_with_colon_is_skipped_with_schema_valid_json(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "a:b.py").write_text("def f():\n    return 1 / 0\n")
    p = run_guard_subprocess(["check", str(root), "--format", "json"])
    assert p.returncode == 2, p.stderr  # previously exit 3 with no JSON
    report = json.loads(p.stdout)
    assert validation_errors("report", report) == []
    assert report["coverage"]["skipped_checks"][0]["kind"] == "excluded_path"


# -- A-06: schema rejects contradictory label combinations ------------------------------------
def test_a06_schema_rejects_contradictory_result_scope_pairs():
    _, report, _ = check_json(REPO / "examples" / "python_demo")
    assert validation_errors("report", report) == []
    pairs = [("PROVED_SAFE", "OVERAPPROXIMATE"), ("PROVED_SAFE", "BOUNDED"), ("POSSIBLY_UNSAFE", "EXACT_MODEL"), ("UNKNOWN", "EXACT_MODEL")]
    for result, scope in pairs:
        bad = json.loads(json.dumps(report))
        target = next(o for o in bad["obligation_results"] if o["property_result"] == result)
        target["scope_validity"] = scope
        assert validation_errors("report", bad), (result, scope)
    bad = json.loads(json.dumps(report))
    bad["findings"][0]["patch_relation"] = "INTRODUCED"
    assert validation_errors("report", bad)  # guard check never attributes a patch relation


# -- A-07: a user-dispatched division says nothing about its divisor -----------------------------
def test_a07_dynamic_dispatch_division_does_not_refine_divisor(tmp_path):
    src = "def f(x, d):\n    y = x / d\n    return 1 / d\n"
    root = _proj(tmp_path, src, ["d"])
    _, report, _ = check_json(root)
    r = result_for(report, "m.py::f", 1)
    assert r["property_result"] != "PROVED_SAFE", r  # was PROVED_SAFE before the fix
    check = (
        "import runpy, sys\nns = runpy.run_path(sys.argv[1])\n"
        "class X:\n    def __truediv__(self, o):\n        return 0\n"
        "try:\n    ns['f'](X(), 0)\nexcept ZeroDivisionError as e:\n    print(e.__traceback__.tb_next.tb_frame.f_lineno)\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", check, str(root / "m.py")], capture_output=True, text=True).stdout.strip()
    assert out == "3"  # CPython fails at the second division


# -- A-08: annotation-derived float types make results conditional --------------------------------
def test_a08_float_annotation_in_cone_is_conditional(tmp_path):
    src = "def f(x: float, d):\n    y = x / d\n    return 1 / d\n"
    root = _proj(tmp_path, src, ["d"])
    _, report, _ = check_json(root)
    r = result_for(report, "m.py::f", 1)
    assert r["property_result"] == "PROVED_SAFE"
    assert r["scope_validity"] == "CONDITIONAL"  # was EXACT_MODEL before the fix
    assert any("x: float" in a for a in r["assumptions"])


def test_a08_conditional_results_always_state_assumptions():
    _, report, _ = check_json(REPO / "examples" / "python_demo")
    for o in report["obligation_results"]:
        if o["scope_validity"] == "CONDITIONAL":
            assert o["assumptions"], o["obligation_id"]


# -- Adversarial edge cases requested by the audit brief ------------------------------------------
EDGE_CASES = [
    # (id, source, declared?, ordinal, expected result, expected scope)
    ("self-subtraction", "def f(a):\n    d = a - a\n    return 1 / d\n", True, 0, "PROVED_UNSAFE", "EXACT_MODEL"),
    ("empty-int-range", "def f(a):\n    if a > 0 and a < 1:\n        return 1 / 0\n    return 0\n", True, 0, "PROVED_SAFE", "EXACT_MODEL"),
    ("nested-safe", "def f(a, b):\n    if a > 0:\n        if b > a:\n            return 1 / (b - a)\n    return 0\n", True, 0, "PROVED_SAFE", "EXACT_MODEL"),
    ("nested-unsafe", "def f(a, b):\n    if a > 0:\n        if b >= a:\n            return 1 / (b - a)\n    return 0\n", True, 0, "PROVED_UNSAFE", "EXACT_MODEL"),
    ("reassign-after-guard", "def f(d):\n    if d != 0:\n        d = d - 1\n        return 1 / d\n    return 0\n", True, 0, "PROVED_UNSAFE", "EXACT_MODEL"),
    ("guard-then-reassign-safe", "def f(d):\n    if d > 0:\n        d = d + 1\n        return 1 / d\n    return 0\n", True, 0, "PROVED_SAFE", "EXACT_MODEL"),
    ("division-by-bool-annotation", "def f(b: bool):\n    return 1 / b\n", False, 0, "PROVED_UNSAFE", "CONDITIONAL"),
    ("division-by-bool-literal", "def f():\n    return 1 // False\n", False, 0, "PROVED_UNSAFE", "EXACT_MODEL"),
    ("bool-arith-is-int", "def f():\n    return 1 % (True + True - 2)\n", False, 0, "PROVED_UNSAFE", "EXACT_MODEL"),
    ("floordiv-minus-one", "def f(a):\n    return a // -1\n", True, 0, "PROVED_SAFE", "EXACT_MODEL"),
    ("floordiv-minus-one-result", "def f(a):\n    q = a // -1\n    return 7 % q\n", True, 1, "PROVED_UNSAFE", "EXACT_MODEL"),
    ("floor-not-trunc", "def f(a):\n    return 1 / (a // 2 + 1)\n", True, 0, "PROVED_UNSAFE", "EXACT_MODEL"),  # a in {-2, -1}; ordinals are pre-order
    ("mod-sign-of-divisor", "def f(a):\n    r = a % -3\n    return 1 / (r - 1)\n", True, 1, "PROVED_SAFE", "EXACT_MODEL"),
    ("walrus", "def f(a):\n    if (d := a - 1):\n        return 1 / d\n    return 0\n", True, 0, "UNKNOWN", None),
    ("global-mutation", "G = 1\ndef f(a):\n    global G\n    G = a\n    return 1 / G\n", True, 0, "UNKNOWN", None),
    # M2: a module constant bound once resolves (I-033); CPython raises at f() and the witness is replayed below.
    ("global-read", "G = 0\ndef f():\n    return 1 / G\n", False, 0, "PROVED_UNSAFE", "EXACT_MODEL"),
    ("global-read-rebound", "G = 0\nG = 1\ndef f():\n    return 1 / G\n", False, 0, "UNKNOWN", None),
    ("global-read-conditional", "import os\nif os.sep:\n    G = 1\ndef f():\n    return 1 / G\n", False, 0, "UNKNOWN", None),
    ("join-drops-one-branch-fact", "def f(a):\n    d = a\n    if a > 0:\n        d = 5\n    return 1 / d\n", True, 0, "PROVED_UNSAFE", "EXACT_MODEL"),
    ("join-both-branches-nonzero", "def f(a):\n    if a > 0:\n        d = a\n    else:\n        d = 1 - a\n    return 1 / d\n", True, 0, "PROVED_SAFE", "EXACT_MODEL"),
    ("negated-guard", "def f(a):\n    if not a:\n        return 1 / a\n    return 0\n", True, 0, "PROVED_UNSAFE", "EXACT_MODEL"),
    ("chained-guard", "def f(a):\n    if 0 < a < 5:\n        return 1 / a\n    return 0\n", True, 0, "PROVED_SAFE", "EXACT_MODEL"),
    ("assert-guard-disabled-by-O", "def f(a):\n    assert a != 0\n    return 1 / a\n", True, 0, "PROVED_UNSAFE", "CONDITIONAL"),
    ("unreachable-after-raise", "def f(a):\n    raise ValueError\n    return 1 / 0\n", True, 0, "PROVED_SAFE", "EXACT_MODEL"),
    ("is-none-on-declared-int", "def f(a):\n    if a is None:\n        return 1 / 0\n    return 1\n", True, 0, "PROVED_SAFE", "EXACT_MODEL"),
    # Loops (I-132): a=0 runs no iteration, so a stays 0; `while True` without break never exits.
    ("loop-carried-parameter", "def f(a):\n    for i in range(a):\n        a = a - 1\n    return 1 / a\n", True, 0, "PROVED_UNSAFE", "EXACT_MODEL"),
    ("infinite-loop-before-literal-zero", "def f():\n    while True:\n        pass\n    return 1 / 0\n", False, 0, "PROVED_SAFE", "EXACT_MODEL"),
    ("try-unsupported", "def f(a):\n    try:\n        return 1 / a\n    except ZeroDivisionError:\n        return 0\n", True, 0, "UNKNOWN", None),
    ("unknown-call-dividend", "def f(d):\n    return g() / d\n", True, 0, "UNKNOWN", None),
    ("negative-zero-float-literal", "def f(a):\n    return a / -0.0\n", True, 0, "UNKNOWN", None),
    ("zero-float-literal", "def f(a):\n    return a / 0.0\n", True, 0, "PROVED_UNSAFE", "EXACT_MODEL"),
    ("unicode-name", "def f(größe):\n    s = 'Δ≠0'\n    return 1 / größe\n", True, 0, "PROVED_UNSAFE", "EXACT_MODEL"),
]


@pytest.mark.parametrize("case_id,src,declared,ordinal,result,scope", EDGE_CASES, ids=[c[0] for c in EDGE_CASES])
def test_adversarial_edge_case(tmp_path, case_id, src, declared, ordinal, result, scope):
    root = _proj(tmp_path, src, declare=declared)
    _, report, _ = check_json(root)
    assert report["errors"] == []
    r = result_for(report, "m.py::f", ordinal)
    assert (r["property_result"], r["scope_validity"]) == (result, scope), r["reasons"]
    if result == "PROVED_UNSAFE":
        optimize = r["runtime_mode"] == "python -O (assertions disabled)"
        assert _replay(root / "m.py", "f", r["witness"], optimize) == "ZeroDivisionError"


def test_unicode_span_columns_are_utf8_bytes(tmp_path):
    src = "def f(größe):\n    s = 'Δ≠0'; return 1 / größe\n"
    root = _proj(tmp_path, src)
    _, report, _ = check_json(root)
    sp = result_for(report, "m.py::f")["source_span"]
    line = src.splitlines()[1].encode("utf-8")
    assert line[sp["start_column"]:sp["end_column"]].decode("utf-8") == "1 / größe"
    assert sp["start_column"] != src.splitlines()[1].index("1 / größe")  # bytes differ from characters


def test_syntax_error_is_incomplete_not_error_and_other_files_still_analyzed(tmp_path):
    root = _proj(tmp_path, "def f(a):\n    return 1 / (a - a)\n")
    (root / "bad.py").write_text("def broken(:\n")
    code, report, _ = check_json(root)
    assert code == 1 and report["verdict"] == "BLOCK" and report["run_outcome"] == "INCOMPLETE"
    assert result_for(report, "m.py::f")["property_result"] == "PROVED_UNSAFE"
    assert report["coverage"]["skipped_checks"][0]["kind"] == "parse_failure"


# -- Safety: the checked repository is never mutated; no execution primitives in the engine --------
def test_checked_repository_is_not_mutated(tmp_path):
    root = _proj(tmp_path, "import os\nos.makedirs('PWNED', exist_ok=True)\n\ndef f(a):\n    return 1 / a\n")
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("raise SystemExit('imported!')\n")

    def state():
        out = {}
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                p = Path(dirpath) / name
                st = p.lstat()
                out[str(p.relative_to(root))] = (st.st_mode, st.st_size, st.st_mtime_ns, p.read_bytes() if p.is_file() else None)
        return out

    before = state()
    for fmt in ("json", "human"):
        p = run_guard_subprocess(["check", str(root), "--format", fmt], cwd=root)
        assert p.returncode in (1, 2), p.stderr
    assert state() == before  # no new files (no __pycache__, no .guard state), nothing modified


FORBIDDEN_BUILTINS = {"eval", "exec", "compile", "__import__", "breakpoint"}
FORBIDDEN_ATTRS = {"system", "popen", "Popen", "check_output", "check_call", "run_path", "run_module", "import_module",
                   "execv", "execve", "execvp", "spawnv", "spawnl", "startfile", "fork"}
FORBIDDEN_MODULES = {"subprocess", "runpy", "pickle", "marshal", "ctypes", "socket", "urllib", "http", "multiprocessing"}


# M2: the read-only Git reader is the single module allowed to start a process (the `git`
# executable, argument arrays only); `test_git_reader_uses_argument_arrays_only` pins how.
# M3: `execution/process.py` is the only other one (the explicitly enabled runner: docker CLI or
# the unisolated harness subprocess); `test_execution_process_uses_argument_arrays_only` pins how.
# `execution/probe_harness.py` is never imported by the engine: it is shipped as data and runs
# only inside the runner boundary, where importing and calling the target is its purpose
# (`test_probe_harness_is_never_imported_by_the_engine`).
# M4: `semantic/endpoint.py` is the only module allowed network primitives: `http.client` +
# `socket` to one validated loopback endpoint and `urllib.parse` for URL parsing
# (`test_model_transport_is_loopback_http_client_only` pins it; no urllib.request, no proxies).
# I-105: `semantic/remote.py` (opt-in bring-your-own-key proposers) is the second one: `http.client`
# over verified TLS to an explicitly allowed host, or plain http to a literal loopback address
# (`test_model_transport_is_loopback_http_client_only` pins both modules).
SUBPROCESS_ALLOWLIST = {"snapshots/git.py": {"subprocess"}, "execution/process.py": {"subprocess"},
                        "execution/probe_harness.py": {"socket"}, "semantic/endpoint.py": {"socket", "http", "urllib"},
                        "semantic/remote.py": {"socket", "http", "urllib"}}
ATTR_ALLOWLIST = {"execution/process.py": {"Popen"}, "execution/probe_harness.py": {"import_module", "fork"},
                  "execution/replay_harness.py": {"import_module", "fork"}}
# Phase 2 (I-117): the replay harness runs only inside the isolation boundary (never imported by
# the engine) and evaluates repository-authored expressions whose text the host verified in
# the snapshot. `test_replay_harness_evaluates_only_repository_inputs` pins where.
BUILTIN_ALLOWLIST = {"execution/replay_harness.py": {"eval", "compile"}}


def test_engine_source_has_no_execution_primitives():
    """Static scan of the engine: candidate strings are never evaluated and nothing is executed.

    The only dynamic import allowed is the optional solver (`importlib.import_module("z3")`).
    """
    offenders = []
    src_root = REPO / "src" / "invariantguard"
    for path in sorted(src_root.rglob("*.py")):
        rel = path.relative_to(src_root).as_posix()
        allowed = SUBPROCESS_ALLOWLIST.get(rel, set())
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                if any(n.split(".")[0] in FORBIDDEN_MODULES - allowed for n in names):
                    offenders.append((path.name, node.lineno, names))
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name) and fn.id in FORBIDDEN_BUILTINS - BUILTIN_ALLOWLIST.get(rel, set()):
                    offenders.append((path.name, node.lineno, fn.id))
                if isinstance(fn, ast.Attribute) and fn.attr in FORBIDDEN_ATTRS - ATTR_ALLOWLIST.get(rel, set()):
                    z3_import = fn.attr == "import_module" and isinstance(node.args[0], ast.Constant) and node.args[0].value == "z3"
                    if not z3_import:
                        offenders.append((path.name, node.lineno, fn.attr))
    assert offenders == []


def test_replay_harness_evaluates_only_repository_inputs():
    """eval/compile occur only where repository text is evaluated, and the engine never imports the harness."""
    path = REPO / "src" / "invariantguard" / "execution" / "replay_harness.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    sites = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("eval", "compile", "exec"):
                    sites.append((fn.name, n.func.id))
    # materialize_arg: test/doctest bindings and expressions; generate_job: .pyi stub annotation text.
    assert sorted(sites) == sorted([("generate_job", "compile"), ("generate_job", "eval")] + [("materialize_arg", "compile"), ("materialize_arg", "eval")] * 2)
    for p in (REPO / "src" / "invariantguard").rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ImportFrom) and n.module:
                assert "replay_harness" not in n.module and not any(a.name == "replay_harness" for a in n.names), p
            elif isinstance(n, ast.Import):
                assert not any("replay_harness" in a.name for a in n.names), p


def test_git_reader_uses_argument_arrays_only():
    """The Git reader runs only `git` with an argument list, never a shell or a string command."""
    tree = ast.parse((REPO / "src" / "invariantguard" / "snapshots" / "git.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"]
    assert [c.func.attr for c in calls] == ["run"]
    (run,) = calls
    assert all(k.arg != "shell" for k in run.keywords)
    assert isinstance(run.args[0], ast.Name) and run.args[0].id == "argv"
    argv = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "argv" for t in n.targets))
    assert isinstance(argv.value, ast.List)
    assert isinstance(argv.value.elts[0], ast.Attribute) and argv.value.elts[0].attr == "exe"
    text = (REPO / "src" / "invariantguard" / "snapshots" / "git.py").read_text()
    for forbidden in ("checkout", "reset", "stash", "commit-tree", "update-ref", "hash-object", "--filters", "--textconv", "worktree add"):
        assert f'"{forbidden}' not in text, forbidden


def test_execution_process_uses_argument_arrays_only():
    """The runner starts processes only from argument lists, never a shell, with an explicit environment."""
    path = REPO / "src" / "invariantguard" / "execution" / "process.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"]
    assert [c.func.attr for c in calls] == ["Popen"]
    (popen,) = calls
    kw = {k.arg: k.value for k in popen.keywords}
    assert "shell" not in kw and isinstance(popen.args[0], ast.Name) and popen.args[0].id == "argv"
    assert isinstance(kw["env"], ast.Name) and kw["env"].id == "env"  # never the caller's environment implicitly
    assert isinstance(kw["start_new_session"], ast.Constant) and kw["start_new_session"].value is True  # process-group kill
    importers = sorted(p.relative_to(REPO / "src" / "invariantguard").as_posix() for p in (REPO / "src" / "invariantguard").rglob("*.py")
                       if any(isinstance(n, ast.Import) and any(a.name == "subprocess" for a in n.names)
                              for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))))
    assert importers == ["execution/process.py", "snapshots/git.py"]


def test_probe_harness_is_never_imported_by_the_engine():
    for path in (REPO / "src" / "invariantguard").rglob("*.py"):
        if path.name == "probe_harness.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "probe_harness" not in (node.module or "") and all("probe_harness" not in a.name for a in node.names), path
            if isinstance(node, ast.Import):
                assert all("probe_harness" not in a.name for a in node.names), path



def test_model_transport_is_loopback_http_client_only():
    """(M4) Only semantic/endpoint.py touches the network, via http.client to a validated loopback address."""
    src_root = REPO / "src" / "invariantguard"
    importers = {}
    for path in src_root.rglob("*.py"):
        rel = path.relative_to(src_root).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for n in names:
                    if n.split(".")[0] in ("socket", "http", "urllib", "requests", "httpx", "aiohttp"):
                        importers.setdefault(rel, set()).add(n)
    assert importers == {"execution/probe_harness.py": {"socket"}, "semantic/endpoint.py": {"socket", "http.client", "urllib.parse"},
                         "semantic/remote.py": {"socket", "http.client", "urllib.parse"}}
    # I-105: the remote adapter verifies TLS (default context; never disabled), follows no redirects and uses no proxy-aware client.
    remote = (src_root / "semantic" / "remote.py").read_text(encoding="utf-8")
    for bad in ("CERT_NONE", "check_hostname = False", "check_hostname=False", "_create_unverified_context", "urlopen", "urllib.request",
                "set_tunnel", "getproxies"):
        assert bad not in remote, bad
    assert remote.count("ssl.create_default_context()") == 1 and "HTTPSConnection(self.dest.connect_host" in remote
    text = (src_root / "semantic" / "endpoint.py").read_text(encoding="utf-8")
    assert "HTTPSConnection" not in text and "urlopen" not in text and "getaddrinfo" not in text and "gethostbyname" not in text
    assert "/api/pull" not in text.split("ALLOWED_REQUESTS = ")[1].split(")})")[0]
