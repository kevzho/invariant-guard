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
operation's line, or when that line called ``__rmod__``/``__mod__`` and the exception left from there.
The ``annotations_violated`` set also holds a ``str`` subclass with its own ``__mod__`` and one whose
comparisons and ``upper`` attribute clear every list argument of the call (an effect on *other*
obligations), and every parameter with a default is also omitted (its default is used). A
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


def eq_effect(s: str, xs: list[int]):
    if len(xs) == 0:
        return 0
    s == 'a'
    return xs[0]


def ne_effect(s: str, xs: list[int]):
    if len(xs) == 0:
        return 0
    'a' != s
    return xs[0]


def lt_effect(s: str, xs: list[int]):
    if len(xs) == 0:
        return 0
    s < 'b'
    return xs[0]


def attr_effect(s: str, xs: list[int]):
    if len(xs) == 0:
        return 0
    s.upper
    return xs[0]


def int_eq_effect(n: int, xs: list[int]):
    if len(xs) == 0:
        return 0
    n == 0
    return xs[0]


def div_effect(s: str, xs: list[int]):
    if len(xs) == 0:
        return 0
    s == 'a'
    return 1 / len(xs)


def _compare(s):
    return s == 'a'


def inlined_effect(s: str, xs: list[int]):
    if len(xs) == 0:
        return 0
    _compare(s)
    return xs[0]


def default_none_str(s: str = None):
    n = 0
    if s is None:
        return 1 / n
    return 0


def default_none_int(k: int = None):
    n = 0
    if k is None:
        return 1 // n
    return 0


def default_mismatch(k: int = 'x'):
    if k == 'x':
        return 10 // 0
    return 1


def default_literal_ok(k: int = 0):
    return 10 // (k * k + 1)


def loop_later(s: str, xs: list[int]):
    if len(xs) == 0:
        return 0
    t = 0
    for i in range(2):
        t = xs[0]
        s == 'a'
    return t


def loop_int_later(n: int, xs: list[int]):
    if len(xs) == 0:
        return 0
    r = 0
    for _ in range(2):
        r = 10 // len(xs)
        if n == 3:
            r = 1
    return r


def loop_lt_later(s: str, xs: list[int]):
    if len(xs) == 0:
        return 0
    r = 0
    for _ in range(2):
        r = xs[0]
        if s < "a":
            r = 1
    return r


def while_attr_later(s: str, xs: list[int]):
    if len(xs) == 0:
        return 0
    r = 0
    i = 0
    while i < 2:
        r = 10 // len(xs)
        u = s.upper
        i += 1
    return r


def after_loop(s: str, xs: list[int]):
    if len(xs) == 0:
        return 0
    for i in range(2):
        s == 'a'
    return xs[0]


D = -0.5
K = 3


def default_constant(n: int = D):
    return 10 // (n * 2 + 1)


def default_arith(n: int = 1 - 1.5):
    return 10 // (n * 2 + 1)


def default_constant_ok(n: int = K + 1):
    return 10 // (n * 2 + 1)


class _Num:
    def __init__(self, v):
        self.v = v

    def __mod__(self, o):
        return self.v % o


class C:
    str = _Num

    def meth(self, s: str):
        return s % 0


class COpt:
    class Optional:
        def __class_getitem__(cls, item):
            return float

    def meth(self, x: Optional[int]):
        if x is None:
            return 0
        return 10 // (x * 2 + 1)


class _RModZero(str):
    def __rmod__(self, other):
        return 1 // 0


class _StrZero:
    def __str__(self):
        return str(1 // 0)

    __repr__ = __str__


class _StrSub(str):
    pass


class _ModZero(str):
    def __mod__(self, other):
        return 1 // 0


class _Mutator(str):
    """Clears every list argument of the call (``targets``, set by the ground truth) from an
    overriding comparison or attribute: the annotation, not the cone, keeps such code out."""
    targets = ()

    def _clear(self):
        for t in self.targets:
            t.clear()

    def __eq__(self, other):
        self._clear()
        return False

    __hash__ = str.__hash__

    def __ne__(self, other):
        self._clear()
        return True

    def __lt__(self, other):
        self._clear()
        return False

    @property
    def upper(self):
        self._clear()
        return str.upper.__get__(self)


class _IntMutator(int):
    targets = ()

    def __eq__(self, other):
        for t in self.targets:
            t.clear()
        return False

    __hash__ = int.__hash__
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
extra = [ns[k]("a") for k in ("_StrSub", "_ModZero", "_Mutator") if k in ns] + ([ns["_IntMutator"](1)] if "_IntMutator" in ns else [])
DEFAULT = object()  # call without this argument: the function's own default is used

def domain(p, mode):
    return ([DEFAULT] if p.default is not inspect.Parameter.empty else []) + _domain(p, mode)

def _domain(p, mode):
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
    if typing.get_origin(a) is list:
        return [[], [1], [1, 2]]
    return base + (rmod if mode == "no_rmod_assumption" else [])

def admitted(p, x):
    if x is DEFAULT or p.annotation is inspect.Parameter.empty:
        return True
    return any(type(x) is type(y) and x == y for y in _domain(p, "admitted"))  # type first: no user __eq__

out = {}
single = set()  # (function, line, exception, parameter): fails with only that annotation violated
for mode in ("admitted", "no_rmod_assumption", "annotations_violated", "unrestricted"):
    fails = set()
    for name, fn in ns.items():
        if name.startswith("_") or not inspect.isfunction(fn) or fn.__module__ != "<run_path>":
            continue
        params = list(inspect.signature(fn).parameters.values())
        for args in itertools.product(*[domain(p, mode) for p in params]):
            violated = [p.name for p, x in zip(params, args) if not admitted(p, x)]
            call = {p.name: list(x) if isinstance(x, list) else x for p, x in zip(params, args) if x is not DEFAULT}
            for x in call.values():
                if isinstance(x, (ns.get("_Mutator", ()), ns.get("_IntMutator", ()))):
                    x.targets = [y for y in call.values() if isinstance(y, list)]
            try:
                fn(**call)
            except Exception as e:
                fr = [(t.name, t.lineno) for t in traceback.extract_tb(e.__traceback__) if t.filename == path]
                if not fr:
                    continue
                if fr[-1][0] in ("__rmod__", "__mod__") and len(fr) >= 2:  # the operator itself was replaced
                    key = (fr[-2][0], fr[-2][1], type(e).__name__)
                else:
                    key = (fr[-1][0], fr[-1][1], type(e).__name__)
                fails.add(key)
                if mode == "annotations_violated" and len(violated) == 1:
                    single.add(key + (violated[0],))
    out[mode] = sorted(fails)
out["single_violation"] = sorted(single)
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
    # Review round 1: the annotation that keeps `s == 'a'` free of user code is listed.
    ("eq_effect", "xs[0]"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("ne_effect", "xs[0]"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("lt_effect", "xs[0]"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("attr_effect", "xs[0]"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("int_eq_effect", "xs[0]"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("div_effect", "1 / len(xs)"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("inlined_effect", "xs[0]"): ("PROVED_SAFE", "PROVED_SAFE"),
    # `x: str = None` is Optional[str]: the default call reaches the division.
    ("default_none_str", "1 / n"): ("PROVED_UNSAFE", "PROVED_UNSAFE"),
    ("default_none_int", "1 // n"): ("PROVED_UNSAFE", "PROVED_UNSAFE"),
    # A literal default of another type: the annotation is not trusted.
    ("default_mismatch", "10 // 0"): ("POSSIBLY_UNSAFE", "POSSIBLY_UNSAFE"),
    ("default_literal_ok", "10 // (k * k + 1)"): ("PROVED_SAFE", "PROVED_SAFE"),
    # `str` in the annotation is the class attribute `_Num` (class scope), not builtins.str.
    ("C.meth", "s % 0"): ("UNKNOWN", "UNKNOWN"),
    # Review round 2: a loop body runs before the site's next iteration (and before code
    # after the loop); the annotation that keeps it free of user code is listed.
    ("loop_later", "xs[0]"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("loop_int_later", "10 // len(xs)"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("loop_lt_later", "xs[0]"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("while_attr_later", "10 // len(xs)"): ("PROVED_SAFE", "PROVED_SAFE"),
    ("after_loop", "xs[0]"): ("PROVED_SAFE", "PROVED_SAFE"),
    # A default that folds (module constant, constant arithmetic) to another type: not trusted.
    ("default_constant", "10 // (n * 2 + 1)"): ("UNKNOWN", "UNKNOWN"),
    ("default_arith", "10 // (n * 2 + 1)"): ("UNKNOWN", "UNKNOWN"),
    ("default_constant_ok", "10 // (n * 2 + 1)"): ("PROVED_SAFE", "PROVED_SAFE"),
    # `Optional` bound in the class body is not typing.Optional.
    ("COpt.meth", "10 // (x * 2 + 1)"): ("UNKNOWN", "UNKNOWN"),
}


def _child(script: str, *argv: str) -> object:
    p = subprocess.run([sys.executable, "-I", "-c", script, *argv], capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _check(root: Path) -> dict[tuple[str, str], str]:
    code, rep, _ = check_json(root)
    assert rep["errors"] == [], rep["errors"]
    truth: dict[str, set] = {}
    for mod in ("m.py", "rebound.py"):
        for mode, fails in _child(GROUND_TRUTH, str(root / mod)).items():
            truth.setdefault(mode, set()).update((mod, *f) for f in fails)
    assert ("m.py", "fmt_unannotated", 5, "ZeroDivisionError") in truth["no_rmod_assumption"]  # the adversary is real
    assert ("m.py", "fmt_unannotated", 5, "ZeroDivisionError") not in truth["admitted"]
    # The other adversaries are real too: a str subclass's own __mod__, comparisons and an
    # attribute that clear an aliased list argument, and the functions' own defaults.
    assert ("m.py", "fmt_annotated_left_tuple", 29, "ZeroDivisionError") in truth["annotations_violated"]
    for fn in ("eq_effect", "ne_effect", "lt_effect", "attr_effect", "inlined_effect"):
        assert any(k[1] == fn and k[3] == "IndexError" and k[4] == "s" for k in truth["single_violation"]), fn
    assert any(k[1] == "int_eq_effect" and k[3] == "IndexError" and k[4] == "n" for k in truth["single_violation"])
    assert any(k[1] == "div_effect" and k[3] == "ZeroDivisionError" and k[4] == "s" for k in truth["single_violation"])
    assert any(k[1] == "default_none_str" and k[3] == "ZeroDivisionError" for k in truth["admitted"])
    assert any(k[1] == "default_mismatch" for k in truth["admitted"])
    for fn in ("loop_later", "loop_lt_later", "while_attr_later", "after_loop"):
        assert any(k[1] == fn and k[4] == "s" for k in truth["single_violation"]), fn
    assert any(k[1] == "loop_int_later" and k[4] == "n" for k in truth["single_violation"])
    assert any(k[1] == "default_constant" for k in truth["admitted"]) and any(k[1] == "default_arith" for k in truth["admitted"])
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
            for name in sorted(p for (m, f, ln, ex, p) in truth["single_violation"] if (m, f, ln, ex) == key):
                # Violating only `name`'s annotation breaks the claim: that annotation must be listed.
                assert any(a.startswith(ANNOTATION) and (f"({name}: " in a or f", {name}: " in a) for a in o["assumptions"]), \
                    f"{fn}:{line} fails when only {name}'s annotation is violated, but the assumptions do not name it: {o['assumptions']}"
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


def test_class_scope_annotation_name_is_not_the_builtin(tmp_path: Path):
    """`str` bound in the class body is what a method's annotation names (CPython evaluates
    it there): honoring that annotation reaches a user `__mod__` that divides by zero."""
    root = write_project(tmp_path / "proj", {"m.py": CORPUS})
    probe = "import runpy, sys\nns = runpy.run_path(sys.argv[1])\ntry:\n    ns['C']().meth(ns['_Num'](5))\n    print('null')\nexcept ZeroDivisionError:\n    print('\"ZeroDivisionError\"')\n"
    assert _child(probe, str(root / "m.py")) == "ZeroDivisionError"
    (root / ".git").mkdir()
    _, rep, _ = check_json(root)
    (o,) = [o for o in rep["obligation_results"] if o["symbol_id"].endswith("::C.meth")]
    assert o["property_result"] == "UNKNOWN"
    assert not any(a.startswith(ANNOTATION) for a in o["assumptions"])
    # `Optional` bound in the class body too: `x: Optional[int]` is float there.
    probe = "import runpy, sys\nns = runpy.run_path(sys.argv[1])\ntry:\n    ns['COpt']().meth(-0.5)\n    print('null')\nexcept ZeroDivisionError:\n    print('\"ZeroDivisionError\"')\n"
    assert _child(probe, str(root / "m.py")) == "ZeroDivisionError"
    (o,) = [o for o in rep["obligation_results"] if o["symbol_id"].endswith("::COpt.meth")]
    assert o["property_result"] == "UNKNOWN"
    assert not any(a.startswith(ANNOTATION) for a in o["assumptions"])


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
