"""Generator functions (I-138), checked against CPython.

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


def none_narrowed(x: int | None):
    if x is not None:
        yield 1
        yield x.real


def none_unsafe(x: int | None):
    yield 1
    yield x.real


def check_lambda_default(n: int):
    if n == 0:
        raise ValueError("zero")
    lambda a=(yield): a


def check_def_default(n: int):
    if n == 0:
        raise ValueError("zero")
    def inner(a=(yield)):
        return a


def check_class_base(n: int):
    if n == 0:
        raise ValueError("zero")
    class K((yield)):
        pass


def check_decorator(n: int):
    if n == 0:
        raise ValueError("zero")
    @(yield)
    def inner():
        return 1


def use_check_lambda_default(n: int):
    check_lambda_default(n)
    return 10 // n


def use_check_def_default(n: int):
    check_def_default(n)
    return 10 // n


def use_check_class_base(n: int):
    check_class_base(n)
    return 10 // n


def use_check_decorator(n: int):
    check_decorator(n)
    return 10 // n


def div_before_lambda_default(n: int):
    k = 10 // n
    f = lambda a=(yield): a
    return k


def prestart_square(n: int):
    yield 10 // (n * n + 1)


def literal_only():
    yield 10 // 2
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
domains = {int: list(range(-3, 4)), list: [[], [0], [1, 2]], tuple: [(), (0,), (1, 2)], int | None: [None, -1, 0, 2],
           None: [None, [], [0, 1]]}
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
    for args in itertools.product(*[domains.get(p.annotation, domains[None]) for p in params]):
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

GENERATOR_FUNCTIONS = r"""
import inspect, json, runpy, sys
ns = runpy.run_path(sys.argv[1])
print(json.dumps(sorted(n for n, f in ns.items() if inspect.isgeneratorfunction(f) and f.__module__ == "<run_path>")))
"""

EXC = {"division.zero_denominator": "ZeroDivisionError", "index.out_of_range": "IndexError", "none.attribute_access": "AttributeError"}

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
    ("none_narrowed", "x.real"): ("PROVED_SAFE", False),
    ("none_unsafe", "x.real"): ("PROVED_UNSAFE", False),
    # A yield in a nested def's/lambda's/class's outer-scope parts (defaults, decorators,
    # bases) makes the enclosing function a generator: calling it runs none of its body, so
    # the n == 0 raise does not guard the division (CPython raises ZeroDivisionError).
    ("use_check_lambda_default", "10 // n"): ("PROVED_UNSAFE", False),
    ("use_check_def_default", "10 // n"): ("PROVED_UNSAFE", False),
    ("use_check_class_base", "10 // n"): ("PROVED_UNSAFE", False),
    ("use_check_decorator", "10 // n"): ("PROVED_UNSAFE", False),
    ("div_before_lambda_default", "10 // n"): ("PROVED_UNSAFE", False),
    ("prestart_square", "10 // (n * n + 1)"): ("PROVED_SAFE", False),
    ("literal_only", "10 // 2"): ("PROVED_SAFE", False),
}

# PROVED_SAFE results whose path crosses a yield rely on the consumer not rebinding the
# generator's locals (possible on CPython 3.13 via gi_frame.f_locals, PEP 667).
CROSSES_YIELD = {"guard_before_yield", "len_before_yield_tuple", "loop_safe", "none_narrowed"}
INTROSPECTION = "do not rebind this function's locals via frame introspection"
# Between creating a generator and its first next(), the consumer can rebind the parameters
# through gi_frame.f_locals, so every generator PROVED_SAFE that depends on one lists this.
PRESTART = "via frame introspection (gi_frame.f_locals) before the first next()"

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
    generators = set(_child(GENERATOR_FUNCTIONS, str(path)))
    assert {"check_lambda_default", "check_def_default", "check_class_base", "check_decorator", "div_before_lambda_default"} <= generators
    finding = {f["obligation_id"]: f["severity"] for f in rep["findings"]}
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
            if fn in CROSSES_YIELD:
                assert any(INTROSPECTION in a for a in o["assumptions"]) and o["scope_validity"] == "CONDITIONAL", (fn, o["assumptions"])
            if fn in generators:
                prestart = any(PRESTART in a for a in o["assumptions"])
                assert prestart == (fn != "literal_only"), (fn, o["assumptions"])
                assert not prestart or o["scope_validity"] == "CONDITIONAL", (fn, o["scope_validity"])
        if result == "PROVED_UNSAFE":
            assert o["witness_validated"] is True
            assert (o["quantifier"] == GENERATOR_UNSAFE) == (fn in generators), (fn, o["quantifier"])
            out = _child(REPLAY, str(path), fn, json.dumps(o["witness"] or {}))
            assert [fn, line, exc] in out["raised"], f"{fn}: witness {o['witness']} does not raise {exc} at line {line}: {out}"
            if handled:
                assert out["escaped"] != [exc, line], f"{fn}: marked handled, but the {exc} from line {line} escapes"
        if handled:
            assert finding[o["obligation_id"]] == "INFO"
    assert {k: got.get(k) for k in EXPECTED} == EXPECTED
    # Non-vacuity: the consumers really reach the failures the expectations rely on.
    assert ("thrown_into_try", CORPUS.splitlines().index("        yield 10 // d") + 1, "ZeroDivisionError") in raised
    assert ("iterating_runs_body", CORPUS.splitlines().index("        return xs[0]", CORPUS.splitlines().index("def iterating_runs_body(xs: list):")) + 1, "IndexError") in raised


PRESTART_REBIND = r"""
import json, runpy, sys
ns = runpy.run_path(sys.argv[1])
class Z:
    def __mul__(self, o):
        return -1
g = ns["prestart_square"](3)        # an exact int, as the domain requires
if sys.version_info >= (3, 13):
    g.gi_frame.f_locals["n"] = Z()  # consumer code before the body starts (PEP 667 write-through)
else:  # before PEP 667, f_locals is a snapshot; the same rebinding needs an explicit write-back
    import ctypes
    g.gi_frame.f_locals["n"] = Z()
    ctypes.pythonapi.PyFrame_LocalsToFast(ctypes.py_object(g.gi_frame), ctypes.c_int(0))
try:
    next(g)
    print(json.dumps(None))
except Exception as e:
    print(json.dumps(type(e).__name__))
"""


def test_prestart_frame_rebinding_is_a_listed_assumption(tmp_path: Path):
    """The pre-start assumption is load-bearing: a consumer that rebinds a parameter through
    gi_frame.f_locals before the first next() makes the PROVED_SAFE division fail in CPython."""
    path = tmp_path / "m.py"
    path.write_text(CORPUS, encoding="utf-8")
    assert _child(PRESTART_REBIND, str(path)) == "ZeroDivisionError"


REBIND = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import pkg.gens, pkg.a
pkg.gens.consume = lambda lst: lst.clear()  # the call no longer just creates a generator
pkg.a.local_gen = lambda lst: lst.clear()
import pkg.use
out = []
for name, call in (("local_only", lambda: pkg.use.local_only()), ("local_div", lambda: pkg.use.local_div()),
                   ("f_local", lambda: pkg.a.f_local([1, 2, 3]))):
    try:
        call()
        out.append([name, None])
    except Exception as e:
        out.append([name, type(e).__name__])
print(json.dumps(out))
"""


def test_generator_call_records_the_callee_resolution(tmp_path: Path):
    """Stepping over a generator call as "no effect" relies on the name denoting that generator
    function, so the result lists the resolution (CONDITIONAL for an import), like an inlined call."""
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "gens.py").write_text("def consume(lst):\n    yield lst\n", encoding="utf-8")
    (root / "pkg" / "use.py").write_text(
        "from pkg.gens import consume\n\n\ndef local_only() -> int:\n    lst = [1, 2, 3]\n    consume(lst)\n    return lst[2]\n\n\n"
        "def local_div() -> int:\n    lst = [1, 2, 3]\n    consume(lst)\n    return 10 // len(lst)\n", encoding="utf-8")
    (root / "pkg" / "a.py").write_text(
        "def local_gen(lst):\n    yield lst\n\n\ndef f_local(lst: list[int]):\n    if len(lst) > 2:\n        local_gen(lst)\n        return lst[2]\n\n\n"
        "def creates_then_div(n: int):\n    it = local_gen(n)\n    return 10 // (n - n)\n", encoding="utf-8")
    _, rep, _ = check_json(root)
    assert rep["errors"] == [], rep["errors"]
    res = {o["symbol_id"]: o for o in rep["obligation_results"]}
    imported = "import 'consume' in pkg/use.py resolves to pkg/gens.py"
    imported_global = "module global 'consume' is the function defined in pkg/gens.py"
    same_module = "module global 'local_gen' is the function defined in pkg/a.py"
    for sid in ("pkg/use.py::local_only", "pkg/use.py::local_div"):
        o = res[sid]
        assert o["property_result"] == "PROVED_SAFE" and o["scope_validity"] == "CONDITIONAL", o
        assert any(imported in a and imported_global in a for a in o["assumptions"]), o["assumptions"]
    assert res["pkg/a.py::f_local"]["property_result"] == "PROVED_SAFE"
    assert any(same_module in a for a in res["pkg/a.py::f_local"]["assumptions"])
    unsafe = res["pkg/a.py::creates_then_div"]
    assert unsafe["property_result"] == "PROVED_UNSAFE" and any(same_module in a for a in unsafe["assumptions"])
    # The assumption is load-bearing: rebinding the names refutes every one of these claims.
    assert _child(REBIND, str(root)) == [["local_only", "IndexError"], ["local_div", "ZeroDivisionError"], ["f_local", "IndexError"]]


SEEDED = '''
def loop(xs: list, n: int):
    k = 1
    for i in range(n):
        if i > 2:
            xs.popleft()
        elif i > 1:
            ys = [q for q in xs]
        else:
            k = k + 1
        yield 10 // k


def in_try():
    try:
        while True:
            yield 1
    except ValueError:
        pass
    yield 10 // 0


def plain_in_try(n: int):
    d = n
    try:
        while True:
            print()
    except ValueError:
        pass
    return 10 // d
'''


def test_reasons_do_not_depend_on_the_hash_seed(tmp_path: Path):
    """Loop markers, try-handler markers and witness assumptions name the unknown effects on
    the path; they used to come from iterating a set of block ids, so the text varied with
    PYTHONHASHSEED (in_try / plain_in_try: a loop inside a try body)."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "m.py").write_text(SEEDED, encoding="utf-8")
    texts = set()
    for seed in range(8):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONHASHSEED": str(seed)}
        p = subprocess.run([sys.executable, "-m", "invariantguard.cli", "check", str(root), "--format", "json"],
                           capture_output=True, text=True, timeout=300, env=env, cwd=tmp_path)
        rep = json.loads(p.stdout)
        assert rep["obligation_results"], p.stderr
        texts.add(json.dumps([(o["obligation_id"], o["property_result"], o["reasons"], o["assumptions"]) for o in rep["obligation_results"]]))
    assert len(texts) == 1, texts


ASYNC_MUTATION = '''
def shared(xs: list, n: int):
    if len(xs) == 0:
        return 0
    ys = []
    for i in range(n):
        ys = [ys, i]
    return xs[0]


def fresh(n: int):
    xs = [1, 2]
    ys = []
    for i in range(n):
        ys = [ys, i]
    return xs[1]


def started_generator(xs: list):
    try:
        yield 1
    finally:
        xs.clear()
'''

FINALIZER = r"""
import gc, json, sys
sys.path.insert(0, sys.argv[1])
import m
xs = [1]
class Cycle:
    def __del__(self):
        xs.clear()
gc.disable()
gc.collect()  # young count 0: the collection below happens inside the loop, after the check
if sys.argv[2] == "del":
    c = Cycle(); c.me = c; del c  # unreachable cycle; only the cyclic collector frees it
else:
    box = [m.started_generator(xs)]; next(box[0]); box.append(box); del box  # a started generator in a cycle
gc.set_threshold(100); gc.enable()
out = []
for name, call in (("shared", lambda: m.shared(xs, 5000)), ("fresh", lambda: m.fresh(5000))):
    try:
        call()
        out.append([name, None])
    except Exception as e:
        out.append([name, type(e).__name__])
print(json.dumps(out))
"""


def test_asynchronous_list_mutation_is_a_listed_assumption(tmp_path: Path):
    """I-141: garbage collection runs at loop back edges, so a finalizer (here a __del__ in an
    unreachable cycle, and a started generator's finally) can clear a caller's list between the
    length check and the index with no call in the analyzed function. Such a PROVED_SAFE is
    CONDITIONAL on a listed assumption; a list the function created itself is not affected."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "m.py").write_text(ASYNC_MUTATION, encoding="utf-8")
    _, rep, _ = check_json(root)
    assert rep["errors"] == [], rep["errors"]
    res = {o["symbol_id"]: o for o in rep["obligation_results"] if o["obligation_id"].endswith("index.out_of_range#0")}
    note = "while this function runs"
    s = res["m.py::shared"]
    assert s["property_result"] == "PROVED_SAFE" and s["scope_validity"] == "CONDITIONAL", s
    assert any(note in a and "mutates list xs" in a for a in s["assumptions"]), s["assumptions"]
    f = res["m.py::fresh"]
    assert f["property_result"] == "PROVED_SAFE" and not any(note in a for a in f["assumptions"]), f
    # The assumption is load-bearing: CPython refutes the shared claim, not the fresh one.
    for mode in ("del", "generator"):
        assert _child(FINALIZER, str(root), mode) == [["shared", "IndexError"], ["fresh", None]], mode
