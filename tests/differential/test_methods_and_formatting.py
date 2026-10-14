"""Methods (I-130) and printf-style ``str % tuple`` formatting (I-131), checked against CPython.

Every PROVED_SAFE obligation is compared with an exhaustive CPython run in an isolated child
interpreter, over adversarial receivers and arguments, and every PROVED_UNSAFE witness is replayed.
The receivers include arbitrary objects (a method body is reachable with any first argument through
``Cls.method(obj, ...)``). The adversarial arguments include a ``str`` subclass whose ``__rmod__``
divides by zero (CPython tries it before ``str.__mod__``) and an object whose ``__str__`` divides
by zero. A failure is attributed to the last frame of the analyzed file, so a ZeroDivisionError
raised inside a hook is the hook's own obligation, not the formatting operation's.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ..conftest import check_json

ADVERSARIAL = '''

class _RModZero(str):
    def __rmod__(self, other):
        return 1 // 0


class _StrZero:
    def __str__(self):
        return str(1 // 0)

    __repr__ = __str__
'''

GROUND_TRUTH = r"""
import inspect, itertools, json, runpy, sys, traceback
path = sys.argv[1]
ns = runpy.run_path(path)
ints = list(range(-3, 4))
adversarial = [ns["_RModZero"]("s"), ns["_StrZero"](), None, "s", (1, 2)]
receivers = [None, 0, object()]
fails = set()

def run(name, fn, params):
    for args in itertools.product(ints + adversarial, repeat=len(params)):
        try:
            fn(*args)
        except Exception as e:
            fr = [t for t in traceback.extract_tb(e.__traceback__) if t.filename == path][-1]
            fails.add((name, fr.lineno, type(e).__name__))

for name, obj in ns.items():
    if name.startswith("_"):
        continue
    if inspect.isfunction(obj) and obj.__module__ == "<run_path>":
        run(name, obj, list(inspect.signature(obj).parameters))
    elif inspect.isclass(obj):
        for mname, m in vars(obj).items():
            if not inspect.isfunction(m):
                continue
            params = list(inspect.signature(m).parameters)
            receivers_all = receivers + [obj.__new__(obj)]
            for recv in receivers_all:
                run(f"{name}.{mname}", lambda *a, m=m, r=recv: m(r, *a), params[1:])
print(json.dumps(sorted(fails)))
"""

REPLAY = r"""
import inspect, json, runpy, sys, traceback
path, qual, args = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
ns = runpy.run_path(path)
cls, _, meth = qual.rpartition(".")
fn = getattr(ns[cls], meth) if cls else ns[qual]
# A parameter the witness leaves unbound is unconstrained by the claimed failure: bind a value the
# witness does not mention (None for the receiver, 0 otherwise) and expect the same failure.
params = list(inspect.signature(fn).parameters)
call = {p: args.get(p, None if (cls and i == 0) else 0) for i, p in enumerate(params)}
try:
    fn(**call)
    print(json.dumps(["OK", 0]))
except Exception as e:
    frames = [t for t in traceback.extract_tb(e.__traceback__) if t.filename == path]
    print(json.dumps([type(e).__name__, frames[-1].lineno if frames else None]))
"""


def _child(script: str, *argv: str) -> object:
    p = subprocess.run([sys.executable, "-I", "-c", script, *argv], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


# (name, source, {(symbol, line): expected property_result}); source lines count from 1 after dedent.
CASES = [
    ("method_literal_division", """
        class C:
            def f(self):
                return 10 / 0

            def g(self):
                k = 4
                return 10 / k
        """, {("C.f", 4): "PROVED_UNSAFE", ("C.g", 8): "PROVED_SAFE"}),
    ("method_receiver_attribute_is_unknown", """
        class C:
            def f(self):
                return 10 / self.k
        """, {("C.f", 4): "UNKNOWN"}),
    ("method_guarded_parameter", """
        class C:
            def f(self, n):
                if n == 0:
                    return 0.0
                return 10 / n
        """, {("C.f", 6): "UNKNOWN"}),
    ("method_and_module_function_share_a_name", """
        def run(n):
            k = 2
            return 10 / k


        class C:
            def run(self):
                return 10 // 0
        """, {("run", 4): "PROVED_SAFE", ("C.run", 9): "PROVED_UNSAFE"}),
    ("method_call_into_module_function", """
        def helper(d):
            return 10 // d


        class C:
            def f(self):
                return helper(0)
        """, {("helper", 3): "UNKNOWN", ("C.f", 3): "PROVED_UNSAFE"}),
    ("method_free_name_skips_class_scope", """
        K = 0


        class C:
            K = 5

            def f(self):
                return 10 // K
        """, {("C.f", 9): "PROVED_UNSAFE"}),
    ("mangled_private_name_is_unsupported", """
        __z = 1


        class C:
            def f(self):
                return 10 // __z
        """, {("C.f", 7): "UNKNOWN"}),
    ("decorated_method_is_unsupported", """
        class C:
            @staticmethod
            def f(n):
                return 10 // 0
        """, {("C.f", 5): "UNKNOWN"}),
    ("method_of_local_class_is_unsupported", """
        def outer():
            class L:
                def f(self):
                    return 10 // 0
            return L
        """, {("outer.<locals>.L.f", 5): "UNKNOWN"}),
    ("format_tuple_is_not_a_division", """
        def f(a, b):
            return 'a=%s b=%r' % (a, b)


        def g(a):
            m = 'a=%d'
            return m % (a,)
        """, {("f", 3): "PROVED_SAFE", ("g", 8): "PROVED_SAFE"}),
    # I-134: a non-tuple right operand is formatting under the recorded __rmod__ assumption
    # (tests/differential/test_annotations.py checks that the assumption is listed and needed).
    ("format_non_tuple_is_formatting_under_rmod_assumption", """
        def f(v):
            return 'got %r' % v
        """, {("f", 3): "PROVED_SAFE"}),
    # I-134: `s: str` is an exact str under the (listed) annotation assumption.
    ("format_annotated_str_is_conditional", """
        def f(s: str, v):
            return s % (v,)
        """, {("f", 3): "PROVED_SAFE"}),
    # With an exact int, formatting cannot raise, so the later division is reached: PROVED_UNSAFE.
    # With an arbitrary `a`, a raising __str__ would stop execution first: the later division is only
    # POSSIBLY_UNSAFE, exactly as for `a + 1` before it (the formatting itself is still PROVED_SAFE).
    ("format_then_real_division_keeps_the_proof", """
        def f(a: int):
            s = 'ok %s' % (a,)
            return 10 // 0


        def g(a):
            s = 'ok %s' % (a,)
            return 10 // 0
        """, {("f", 3): "PROVED_SAFE", ("f", 4): "PROVED_UNSAFE", ("g", 8): "PROVED_SAFE", ("g", 9): "POSSIBLY_UNSAFE"}),
]


@pytest.mark.parametrize("name,src,expected", CASES, ids=[c[0] for c in CASES])
def test_methods_and_formatting_against_cpython(tmp_path: Path, name, src, expected):
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()  # stop config discovery at this root
    path = root / "m.py"
    path.write_text(textwrap.dedent(src) + ADVERSARIAL, encoding="utf-8")  # line 1 is blank; expected lines count it
    code, rep, _ = check_json(root)
    assert rep["errors"] == [], rep["errors"]
    failing = {(fn, line) for fn, line, exc in _child(GROUND_TRUTH, str(path)) if exc == "ZeroDivisionError"}
    got: dict[tuple[str, int], str] = {}
    for o in rep["obligation_results"]:
        sym = o["symbol_id"].split("::")[1]
        if sym.startswith("_"):
            continue
        line = o["source_span"]["start_line"]
        got[(sym, line)] = o["property_result"]
        if o["property_result"] == "PROVED_SAFE":
            assert (sym, line) not in failing, f"unsound PROVED_SAFE: CPython raises ZeroDivisionError at {sym} line {line}"
        if o["property_result"] == "PROVED_UNSAFE":
            assert o["witness_validated"] is True
            exc, at = _child(REPLAY, str(path), sym, json.dumps(o["witness"] or {}))
            assert (exc, at) == ("ZeroDivisionError", line), (sym, o["witness"], exc, at)
    assert got == expected


def test_non_tuple_right_operand_dispatches_to_user_rmod(tmp_path: Path):
    """Why a non-tuple right operand needs an assumption: with a ``str`` subclass on the right, CPython
    runs the subclass's ``__rmod__`` (user code) instead of ``str.__mod__``. Inside a tuple the same
    object is only formatted. This test only probes CPython; that guard then lists the assumption
    (and reports UNKNOWN with ``assume_no_str_rmod_override = false``) is checked by
    ``test_annotations.py::test_rmod_assumption_is_listed_and_configurable``."""
    path = tmp_path / "m.py"
    path.write_text(ADVERSARIAL, encoding="utf-8")
    probe = (
        "import runpy, sys; ns = runpy.run_path(sys.argv[1]); v = ns['_RModZero']('s')\n"
        "try:\n    'got %r' % v\n    print('str.__mod__')\nexcept ZeroDivisionError:\n    print('__rmod__')\n"
        "print('got %r' % (v,))\n"
    )
    p = subprocess.run([sys.executable, "-I", "-c", probe, str(path)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert p.stdout.split("\n")[:2] == ["__rmod__", "got 's'"]


def test_formatting_width_is_bounded_in_the_evaluator(tmp_path: Path):
    """A solver witness may pick a huge ``*`` width (or the format string may hold a huge literal
    width): the IR evaluator must not build that string. It stops (STUCK), so the division is not
    PROVED_UNSAFE from that evaluation; nothing near the width is ever allocated."""
    from invariantguard.ir.evaluator import MAX_SEQ_LEN, _format_too_wide

    assert _format_too_wide("%*d", (MAX_SEQ_LEN + 1, 1)) and _format_too_wide("%.*f", (-(MAX_SEQ_LEN + 1), 1.0))
    assert _format_too_wide("%99999999999999999999s", "a") and _format_too_wide("%(k)100000s", "a")
    assert not _format_too_wide("%*d|%5.2f", (10, 1, 2.5)) and not _format_too_wide("100%% %s", "a")
    (tmp_path / ".git").mkdir()
    (tmp_path / "m.py").write_text(
        "def wide(n: int):\n    if n > 1000000:\n        t = '%*d' % (n, 1)\n        return 1 / 0\n    return 0\n", encoding="utf-8")
    _, rep, _ = check_json(tmp_path)
    o = next(o for o in rep["obligation_results"] if o["operation_text"] == "1 / 0")
    assert o["property_result"] != "PROVED_UNSAFE" and o["witness_validated"] is False
