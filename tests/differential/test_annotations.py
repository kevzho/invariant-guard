"""``str`` annotations and ``str % x`` formatting (I-134), checked against CPython.

Every function runs exhaustively in an isolated child interpreter over three input sets:

* ``admitted``: annotated parameters get exact values of their annotation, unannotated ones get
  ints, strs, None, a tuple, a list, a float and an object whose ``__str__`` divides by zero;
* ``no_rmod_assumption``: the same, plus a ``str`` subclass whose ``__rmod__`` divides by zero for
  unannotated parameters (CPython calls it *before* ``str.__mod__``);
* ``annotations_violated``: annotated parameters get every value except that subclass (and a plain
  ``str`` subclass), unannotated ones the ``admitted`` values;
* ``unrestricted``: every value for every parameter.

A failure is attributed to the operation when the exception's last frame in the analyzed file is the
operation's line, or when that line called ``__rmod__`` and the exception left from there. A
PROVED_SAFE operation must never fail on ``admitted`` inputs. If it fails on the second set, the
result must list the ``__rmod__`` assumption; on the third, the annotation assumption; on the last,
one of them. With ``assume_no_str_rmod_override = false`` the operations that need
the assumption are UNKNOWN. Every PROVED_UNSAFE witness is replayed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ..conftest import check_json, write_project

CORPUS = '''from typing import Optional


def fmt_unannotated(v):
    return 'got %r' % v


def fmt_attribute(o):
    return '%s!' % o.real


def fmt_int(n: int):
    return 'n=%d' % n


def fmt_optional_int(n: int | None):
    return 'n=%s' % n


def fmt_str_right(s: str):
    return '[%s]' % s


def fmt_annotated_left(s: str, v):
    return s % v


def fmt_annotated_left_tuple(s: str, v):
    return s % (v,)


def fmt_then_divide(v, d: int):
    m = 'x %s' % v
    return 10 // d


def fmt_int_then_divide(n: int, d: int):
    m = 'x %s' % n
    return 10 // d


def str_truthiness(s: str):
    if s:
        return 10 // 0
    return 1


def str_equality(s: str):
    if s == 'a':
        return 10 // 0
    return 1


def optional_str_none(s: str | None):
    if s is None:
        return 10 // 0
    return s.upper()


def optional_str_attribute(s: Optional[str]):
    return s.strip()


def iterate_str(s: str):
    t = 0
    for c in s:
        if c == 'a':
            t = 10 // 0
    return t


def loop_over_str(s: str, d: int):
    for c in s:
        d = d + 1
    return 10 // d


class _RModZero(str):
    def __rmod__(self, other):
        return 1 // 0


class _StrZero:
    def __str__(self):
        return str(1 // 0)

    __repr__ = __str__


class _StrSub(str):
    pass
'''

# A module that rebinds the name ``str``: its annotations are not trusted (dynamic dispatch).
REBOUND = '''str = int


def rebound(s: str, v):
    return s % v
'''

GROUND_TRUTH = r"""
import inspect, itertools, json, runpy, sys, traceback, typing
path = sys.argv[1]
ns = runpy.run_path(path)
ints = list(range(-3, 4))
strs = ["", "a", "0", "%s", "ab"]
others = [None, (1, 2), [1], 1.5, ns.get("_StrZero", lambda: None)()]
rmod = [ns["_RModZero"]("s")] if "_RModZero" in ns else []
extra = [ns["_StrSub"]("a")] if "_StrSub" in ns else []

def domain(p, mode):
    base = ints + strs + others
    if mode == "unrestricted":
        return base + rmod + extra
    a = p.annotation
    if a is not inspect.Parameter.empty and mode == "annotations_violated":
        return base + extra
    if a is int:
        return ints
    if a is str:
        return strs
    if set(typing.get_args(a)) == {str, type(None)}:
        return strs + [None]
    if set(typing.get_args(a)) == {int, type(None)}:
        return ints + [None]
    return base + (rmod if mode == "no_rmod_assumption" else [])

out = {}
for mode in ("admitted", "no_rmod_assumption", "annotations_violated", "unrestricted"):
    fails = set()
    for name, fn in ns.items():
        if name.startswith("_") or not inspect.isfunction(fn) or fn.__module__ != "<run_path>":
            continue
        params = list(inspect.signature(fn).parameters.values())
        for args in itertools.product(*[domain(p, mode) for p in params]):
            try:
                fn(*[list(x) if isinstance(x, list) else x for x in args])
            except Exception as e:
                fr = [(t.name, t.lineno) for t in traceback.extract_tb(e.__traceback__) if t.filename == path]
                if not fr:
                    continue
                if fr[-1][0] == "__rmod__" and len(fr) >= 2:
                    fails.add((fr[-2][0], fr[-2][1], type(e).__name__))
                else:
                    fails.add((fr[-1][0], fr[-1][1], type(e).__name__))
    out[mode] = sorted(fails)
print(json.dumps(out))
"""

REPLAY = r"""
import inspect, json, runpy, sys, traceback
path, name, args, filler = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), json.loads(sys.argv[4])
fn = runpy.run_path(path)[name]
call = {p: args.get(p, filler) for p in inspect.signature(fn).parameters}
try:
    fn(**call)
    print(json.dumps(["OK", 0]))
except Exception as e:
    frames = [t for t in traceback.extract_tb(e.__traceback__) if t.filename == path]
    print(json.dumps([type(e).__name__, frames[-1].lineno if frames else None]))
"""

EXC = {"division.zero_denominator": "ZeroDivisionError", "index.out_of_range": "IndexError", "none.attribute_access": "AttributeError"}
RMOD = "the right operand of str % x is not an instance of a str subclass that overrides __rmod__"
ANNOTATION = "annotation-only domain"

# (function, operation text) -> (default result, result with assume_no_str_rmod_override = false)
EXPECTED = {
    ("fmt_unannotated", "'got %r' % v"): ("PROVED_SAFE", "UNKNOWN"),
    ("fmt_attribute", "'%s!' % o.real"): ("PROVED_SAFE", "UNKNOWN"),
    ("fmt_int", "'n=%d' % n"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("fmt_optional_int", "'n=%s' % n"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("fmt_str_right", "'[%s]' % s"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("fmt_annotated_left", "s % v"): ("PROVED_SAFE", "UNKNOWN"),
    ("fmt_annotated_left_tuple", "s % (v,)"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("fmt_then_divide", "'x %s' % v"): ("PROVED_SAFE", "UNKNOWN"),
    # A raising __str__ of `v` may stop execution first, so the evaluator cannot run the witness.
    ("fmt_then_divide", "10 // d"): ("POSSIBLY_UNSAFE", "POSSIBLY_UNSAFE"),
    ("fmt_int_then_divide", "'x %s' % n"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("fmt_int_then_divide", "10 // d"): ("PROVED_UNSAFE", "PROVED_UNSAFE"),
    ("str_truthiness", "10 // 0"): ("PROVED_UNSAFE", "PROVED_UNSAFE"),
    ("str_equality", "10 // 0"): ("PROVED_UNSAFE", "PROVED_UNSAFE"),
    ("optional_str_none", "10 // 0"): ("PROVED_UNSAFE", "PROVED_UNSAFE"),
    ("optional_str_none", "s.upper"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("optional_str_attribute", "s.strip"): ("PROVED_UNSAFE", "PROVED_UNSAFE"),
    ("iterate_str", "10 // 0"): ("PROVED_UNSAFE", "PROVED_UNSAFE"),
    ("loop_over_str", "10 // d"): ("PROVED_UNSAFE", "PROVED_UNSAFE"),
    ("rebound", "s % v"): ("UNKNOWN", "UNKNOWN"),
}


def _child(script: str, *argv: str) -> object:
    p = subprocess.run([sys.executable, "-I", "-c", script, *argv], capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _check(root: Path) -> dict[tuple[str, str], str]:
    code, rep, _ = check_json(root)
    assert rep["errors"] == [], rep["errors"]
    truth = {}
    for mod in ("m.py", "rebound.py"):
        for mode, fails in _child(GROUND_TRUTH, str(root / mod)).items():
            truth.setdefault(mode, set()).update((mod, fn, line, exc) for fn, line, exc in fails)
    assert ("m.py", "fmt_unannotated", 5, "ZeroDivisionError") in truth["no_rmod_assumption"]  # the adversary is real
    assert ("m.py", "fmt_unannotated", 5, "ZeroDivisionError") not in truth["admitted"]
    got: dict[tuple[str, str], str] = {}
    for o in rep["obligation_results"]:
        mod, fn = o["symbol_id"].split("::")
        mod = mod.rsplit("/", 1)[-1]
        if fn.startswith("_"):
            continue
        line, result, exc = o["source_span"]["start_line"], o["property_result"], EXC[o["property_id"]]
        got[(fn, o["operation_text"])] = result
        key = (mod, fn, line, exc)
        rmod_listed = any(a.startswith(RMOD) for a in o["assumptions"])
        if result == "PROVED_SAFE":
            assert key not in truth["admitted"], f"unsound PROVED_SAFE: CPython raises {exc} in {fn} at line {line}"
            if key in truth["no_rmod_assumption"]:
                assert rmod_listed and o["scope_validity"] == "CONDITIONAL", f"{fn}:{line} fails through __rmod__ but the claim does not list the assumption"
            if key in truth["annotations_violated"]:
                assert o["scope_validity"] == "CONDITIONAL" and any(a.startswith(ANNOTATION) for a in o["assumptions"]), \
                    f"{fn}:{line} fails when an annotation is violated but the claim does not list the annotation assumption"
            if key in truth["unrestricted"]:
                assert o["scope_validity"] == "CONDITIONAL" and (rmod_listed or any(a.startswith(ANNOTATION) for a in o["assumptions"])), \
                    f"{fn}:{line} fails outside the annotations but the claim lists no assumption: {o['assumptions']}"
        if result == "PROVED_UNSAFE":
            assert o["witness_validated"] is True
            for filler in (0, None, ""):
                out = _child(REPLAY, str(root / mod), fn, json.dumps(o["witness"] or {}), json.dumps(filler))
                assert out == [exc, line], f"{fn}: witness {o['witness']} (filler {filler!r}) gives {out}, expected {exc} at line {line}"
    return got


def test_annotations_against_cpython(tmp_path: Path):
    root = write_project(tmp_path / "proj", {"m.py": CORPUS, "rebound.py": REBOUND})
    (root / ".git").mkdir()  # stop config discovery at this root
    got = _check(root)
    assert {k: got.get(k) for k in EXPECTED} == {k: v[0] for k, v in EXPECTED.items()}

    off = write_project(tmp_path / "off", {"m.py": CORPUS, "rebound.py": REBOUND},
                        config='schema_version = "1.0"\n[analysis]\nassume_no_str_rmod_override = false\n')
    (off / ".git").mkdir()
    got_off = _check(off)
    assert {k: got_off.get(k) for k in EXPECTED} == {k: v[1] for k, v in EXPECTED.items()}


def test_rmod_assumption_is_listed_and_configurable(tmp_path: Path):
    root = write_project(tmp_path / "proj", {"m.py": "def f(v):\n    return 'got %r' % v\n"})
    (root / ".git").mkdir()
    _, rep, _ = check_json(root)
    (o,) = rep["obligation_results"]
    assert (o["property_result"], o["method"], o["scope_validity"]) == ("PROVED_SAFE", "TYPE_FACT", "CONDITIONAL")
    assert any(a.startswith(RMOD) for a in o["assumptions"])
    (root / ".guard").mkdir()
    (root / ".guard" / "config.toml").write_text('schema_version = "1.0"\n[analysis]\nassume_no_str_rmod_override = false\n', encoding="utf-8")
    _, rep, _ = check_json(root)
    (o,) = rep["obligation_results"]
    assert o["property_result"] == "UNKNOWN"
    assert "assume_no_str_rmod_override = false" in o["reasons"][0]
