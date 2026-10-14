"""Generator functions (I-135), checked against CPython.

The ground truth traces CPython (``sys.settrace`` exception events, as in test_try.py) while
it consumes every generator in many ways: ``list(...)``; partial consumption that stops,
``close()``s or ``throw()``s a ValueError in after k resumptions; resumption with ``send``
of adversarial values (0, -1, a str, a list); and a consumer that clears every list
argument between resumptions (consumer code may mutate shared objects). Every PROVED_SAFE
obligation must never raise its exception under any of these consumers. Every
PROVED_UNSAFE witness must raise it when the generator is consumed with ``list(...)``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ..conftest import check_json

CORPUS = '''
def plain(a: int, b: int):
    yield a
    yield a // b


def guarded(n: int):
    if n == 0:
        return
    yield 10 // n


def guard_before_yield(n: int):
    if n != 0:
        yield 1
        yield 10 // n


def sent_ignored(n: int):
    x = yield n
    yield 10 // n


def sent_divisor(n: int):
    x = yield n
    yield 10 // x


def len_before_yield(xs: list):
    if len(xs) > 0:
        yield 0
        yield xs[0]


def len_before_yield_tuple(xs: tuple):
    if len(xs) > 0:
        yield 0
        yield xs[0]


def len_after_yield(xs: list):
    yield 0
    if len(xs) > 0:
        yield xs[0]


def loop_safe(n: int):
    for i in range(n):
        yield 10 // (i + 1)


def loop_unsafe(n: int):
    for i in range(-2, n):
        yield 10 // i


def while_countdown(n: int):
    k = n
    while k > 0:
        yield k
        k = k - 1
    yield 10 // k


def thrown_into_try(n: int):
    d = 1
    try:
        yield 1
        d = 0
        yield 2
    except ValueError:
        yield 10 // d


def close_runs_finally(n: int):
    d = 1
    try:
        yield 1
        d = n
        yield 2
    finally:
        x = 10 // d


def handled_in_generator(a: int, b: int):
    try:
        yield a // b
    except ZeroDivisionError:
        yield 0


def yield_from_list(xs: list, n: int):
    yield from xs
    yield 10 // n


def yield_from_unknown(it, n: int):
    yield from it
    yield 10 // n


def return_value(n: int):
    yield 1
    return 10 // n


def creates_generator(n: int):
    g = guarded(n)
    return 10 // (n + 1)


def creation_keeps_list_facts(xs: list):
    if len(xs) > 0:
        g = plain(1, 1)
        return xs[0]


def clears(xs: list):
    xs.clear()
    yield 1


def iterating_runs_body(xs: list):
    if len(xs) > 0:
        for v in clears(xs):
            pass
        return xs[0]
'''

TRACER = r"""
import sys
path = sys.argv[1]
raised, seen, keep = set(), set(), []

def tracer(frame, event, arg):
    if event == "exception" and frame.f_code.co_filename == path and id(arg[1]) not in seen:
        seen.add(id(arg[1]))
        keep.append(arg[1])  # keep alive: ids must stay unique
        raised.add((frame.f_code.co_name, frame.f_lineno, type(arg[1]).__name__))
    return tracer
"""

GROUND_TRUTH = TRACER + r"""
import inspect, itertools, json, runpy
ns = runpy.run_path(path)
domains = {int: list(range(-3, 4)), list: [[], [0], [1, 2]], tuple: [(), (0,), (1, 2)], None: [None, [], [0, 1]]}
SENT = [None, 0, -1, "x", []]
MAX_STEPS = 60

def drive(g, k, value, action, lists, mutate):
    # next() once, then resume with send(value); at resumption k: stop, close() or throw().
    try:
        next(g)
        step = 1
        while step < MAX_STEPS:
            if mutate:
                for xs in lists:
                    xs.clear()
            if step == k and action == "stop":
                return
            if step == k and action == "close":
                g.close()
                return
            if step == k and action == "throw":
                g.throw(ValueError("thrown by the consumer"))
            else:
                g.send(value if not isinstance(value, list) else list(value))
            step += 1
    except Exception:
        pass

def consumers():
    yield "list", None, None, False
    for mutate in (False, True):
        for value in SENT:
            yield "drive", (99, value, "exhaust"), None, mutate
            for k in range(1, 5):
                for action in ("stop", "close", "throw"):
                    yield "drive", (k, value, action), None, mutate

for name, fn in ns.items():
    if not inspect.isfunction(fn) or fn.__module__ != "<run_path>":
        continue
    params = list(inspect.signature(fn).parameters.values())
    for args in itertools.product(*[domains[p.annotation if p.annotation in (int, list, tuple) else None] for p in params]):
        plans = consumers() if inspect.isgeneratorfunction(fn) else [("call", None, None, False)]
        for kind, plan, _, mutate in plans:
            call = [list(a) if isinstance(a, list) else a for a in args]
            lists = [a for a in call if isinstance(a, list)]
            sys.settrace(tracer)
            try:
                if kind == "call":
                    fn(*call)
                elif kind == "list":
                    list(itertools.islice(fn(*call), MAX_STEPS))
                else:
                    g = fn(*call)
                    drive(g, *plan, lists, mutate)
                    g.close()  # finalize while traced (an abandoned generator is closed on collection)
            except Exception:
                pass
            finally:
                sys.settrace(None)
print(json.dumps(sorted(raised)))
"""

REPLAY = TRACER + r"""
import inspect, json, runpy, traceback
name, args = sys.argv[2], json.loads(sys.argv[3])
fn = runpy.run_path(path)[name]
call = {p: args.get(p) for p in inspect.signature(fn).parameters}
sys.settrace(tracer)
try:
    if inspect.isgeneratorfunction(fn):
        list(fn(**call))  # the modeled consumer: next() until exhaustion
    else:
        fn(**call)
    escaped = None
except Exception as e:
    frames = [t for t in traceback.extract_tb(e.__traceback__) if t.filename == path]
    escaped = [type(e).__name__, frames[-1].lineno if frames else None]
finally:
    sys.settrace(None)
print(json.dumps({"raised": sorted(raised), "escaped": escaped}))
"""

EXC = {"division.zero_denominator": "ZeroDivisionError", "index.out_of_range": "IndexError"}

# (function, operation text) -> (property_result, handled)
EXPECTED = {
    ("plain", "a // b"): ("PROVED_UNSAFE", False),
    ("guarded", "10 // n"): ("PROVED_SAFE", False),
    ("guard_before_yield", "10 // n"): ("PROVED_SAFE", False),
    ("sent_ignored", "10 // n"): ("PROVED_UNSAFE", False),
    ("sent_divisor", "10 // x"): ("UNKNOWN", False),
    ("len_before_yield", "xs[0]"): ("UNKNOWN", False),
    ("len_before_yield_tuple", "xs[0]"): ("PROVED_SAFE", False),
    # Safe in CPython (no resumption between the check and the index), but any unknown effect
    # earlier on the path makes a list index UNKNOWN (existing conservative list rule).
    ("len_after_yield", "xs[0]"): ("UNKNOWN", False),
    ("loop_safe", "10 // (i + 1)"): ("PROVED_SAFE", False),
    ("loop_unsafe", "10 // i"): ("PROVED_UNSAFE", False),
    ("while_countdown", "10 // k"): ("PROVED_UNSAFE", False),
    ("thrown_into_try", "10 // d"): ("POSSIBLY_UNSAFE", False),
    ("close_runs_finally", "10 // d"): ("PROVED_UNSAFE", False),
    ("handled_in_generator", "a // b"): ("PROVED_UNSAFE", True),
    ("yield_from_list", "10 // n"): ("PROVED_UNSAFE", False),
    ("yield_from_unknown", "10 // n"): ("POSSIBLY_UNSAFE", False),
    ("return_value", "10 // n"): ("PROVED_UNSAFE", False),
    ("creates_generator", "10 // (n + 1)"): ("PROVED_UNSAFE", False),
    ("creation_keeps_list_facts", "xs[0]"): ("PROVED_SAFE", False),
    ("iterating_runs_body", "xs[0]"): ("UNKNOWN", False),
}

GENERATOR_UNSAFE = "exists admitted function input such that consuming the generator with next() until exhaustion reaches the failure"


def _child(script: str, *argv: str) -> object:
    p = subprocess.run([sys.executable, "-I", "-c", script, *argv], capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_generators_against_cpython(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()  # stop config discovery at this root
    path = root / "m.py"
    path.write_text(CORPUS, encoding="utf-8")
    code, rep, _ = check_json(root)
    assert rep["errors"] == [], rep["errors"]
    assert not [u for u in rep["coverage"]["unsupported_symbols"] if "generator" in u["reason"]]
    raised = {tuple(r) for r in _child(GROUND_TRUTH, str(path))}
    generators = {"clears"} | {fn for fn, _ in EXPECTED if fn not in ("creates_generator", "creation_keeps_list_facts", "iterating_runs_body")}
    got: dict[tuple[str, str], tuple[str, bool]] = {}
    for o in rep["obligation_results"]:
        fn = o["symbol_id"].split("::")[1]
        line, result, exc = o["source_span"]["start_line"], o["property_result"], EXC[o["property_id"]]
        handled = not o["required"] and any("handled" in r for r in o["reasons"])
        got[(fn, o["operation_text"])] = (result, handled)
        if result == "PROVED_SAFE":
            assert (fn, line, exc) not in raised, f"unsound PROVED_SAFE: CPython raises {exc} in {fn} at line {line}"
            if fn in generators:
                assert "every consumer" in o["quantifier"]
        if result == "PROVED_UNSAFE":
            assert o["witness_validated"] is True
            assert (o["quantifier"] == GENERATOR_UNSAFE) == (fn in generators), (fn, o["quantifier"])
            out = _child(REPLAY, str(path), fn, json.dumps(o["witness"] or {}))
            assert [fn, line, exc] in out["raised"], f"{fn}: witness {o['witness']} does not raise {exc} at line {line}: {out}"
            if handled:
                assert out["escaped"] != [exc, line], f"{fn}: marked handled, but the {exc} from line {line} escapes"
    assert {k: got.get(k) for k in EXPECTED} == EXPECTED
    # Non-vacuity: the consumers really reach the failures the expectations rely on.
    assert ("thrown_into_try", CORPUS.splitlines().index("        yield 10 // d") + 1, "ZeroDivisionError") in raised
    assert ("iterating_runs_body", CORPUS.splitlines().index("        return xs[0]", CORPUS.splitlines().index("def iterating_runs_body(xs: list):")) + 1, "IndexError") in raised
