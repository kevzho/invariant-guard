"""Loops (I-132), checked against CPython.

Every PROVED_SAFE obligation in the corpus is compared with an exhaustive CPython run in an
isolated child interpreter (ints -3..3 for ``int`` parameters, small lists for ``list``
parameters, and ints, None, containers and a context manager for unannotated ones). Every
PROVED_UNSAFE witness is replayed. ``EXPECTED`` additionally pins the precision the loop
model is meant to have, so a regression to UNKNOWN is visible too.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ..conftest import check_json

CORPUS = '''
def harmonic(n: int):
    s = 0
    for i in range(1, n):
        s += 100 // i
    return s


def down(n: int):
    s = 0
    for i in range(n, 0, -1):
        s += 100 // i
    return s


def down_to_zero(n: int):
    s = 0
    for i in range(n, -1, -1):
        s += 100 // i
    return s


def step_two_from_neg(n: int):
    for i in range(-3, n, 2):
        x = 10 // i
    return 0


def step_three_hits_zero(n: int):
    for i in range(-3, n, 3):
        x = 10 // i
    return 0


def computed_step(n: int, k: int):
    for i in range(1, n, k):
        x = 10 // i
    return 0


def carried(n: int):
    d = 3
    for i in range(n):
        d -= 1
    return 10 // d


def while_count(n: int):
    i = 0
    while i < n:
        i += 1
    return 10 // (i - 2)


def while_guard(n: int):
    while n > 0:
        x = 10 // n
        n -= 1
    return 0


def while_true_break(n: int):
    i = 0
    while True:
        if i >= n:
            break
        i += 1
    return 10 // i


def for_else(n: int):
    d = 0
    for i in range(n):
        if i == 2:
            break
    else:
        d = 1
    return 10 // d


def continue_skip(n: int):
    for i in range(-2, n):
        if i == 0:
            continue
        x = 10 // i
    return 0


def nested(n: int):
    t = 0
    for i in range(1, n):
        for j in range(i):
            t += 10 // i
            t += 10 // (i - j)
    return t


def nested_unsafe(n: int):
    for i in range(n):
        for j in range(n):
            x = 10 // (i - j)
    return 0


def len_index(xs: list):
    t = 0
    for i in range(len(xs)):
        t = xs[i]
    return t


def len_index_shrink(xs: list):
    for i in range(len(xs)):
        xs.pop()
        y = xs[i]
    return 0


def iterate_list(xs: list):
    c = 0
    for x in xs:
        c += 1
    return 10 // c


def iterate_str_literal(n: int):
    c = 0
    for ch in "abc":
        c += 1
    return 10 // (c - 3)


def return_in_loop(n: int):
    for i in range(n):
        if i == 1:
            return 10 // (i - 1)
    return 0


def maybe_unbound(n: int):
    for i in range(n):
        last = i
    return 10 // last


def type_change(n: int):
    v = 1
    for i in range(n):
        v = None
    return 10 // v


def tuple_target(n: int):
    t = 0
    for a, b in [(1, 2), (3, 0)]:
        t = 10 // b
    return t


def unknown_iterable(it):
    k = 1
    for x in it:
        k = 0
    return 10 // k


def escape_by_break(n: int, cm):
    d = 1
    for i in range(n):
        d = 0
        with cm:
            break
        d = 1
    return 10 // d


def escape_by_continue(n: int, cm):
    d = 1
    for i in range(n):
        d = 0
        with cm:
            continue
        d = 1
    return 10 // d


def long_loop(n: int):
    s = 0
    for i in range(1000000):
        s += 1
    return 10 // (s - 1000000)


def list_grows_while_iterated(n: int):
    xs = [1]
    c = 0
    for x in xs:
        c += 1
        if c < 3:
            xs += [1]
    return 10 // (c - 3)
'''

ADVERSARIAL = '''

class _CM:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
'''

GROUND_TRUTH = r"""
import inspect, itertools, json, runpy, sys, traceback
path = sys.argv[1]
ns = runpy.run_path(path)
domains = {
    int: list(range(-3, 4)),
    list: [[], [0], [1, 2], [0, 0, 0], [5, -1]],
    None: list(range(-3, 4)) + [None, [], [1], (1, 0), ns["_CM"]()],
}
fails = set()
for name, fn in ns.items():
    if name.startswith("_") or not inspect.isfunction(fn) or fn.__module__ != "<run_path>":
        continue
    params = list(inspect.signature(fn).parameters.values())
    doms = [domains[p.annotation if p.annotation in (int, list) else None] for p in params]
    for args in itertools.product(*doms):
        args = [list(a) if isinstance(a, list) else a for a in args]
        try:
            fn(*args)
        except Exception as e:
            frames = [t for t in traceback.extract_tb(e.__traceback__) if t.filename == path]
            if frames:
                fails.add((name, frames[-1].lineno, type(e).__name__))
print(json.dumps(sorted(fails)))
"""

REPLAY = r"""
import inspect, json, runpy, sys, traceback
path, name, args = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
fn = runpy.run_path(path)[name]
# A parameter the witness leaves out is unconstrained by the claimed failure.
call = {p: args.get(p) for p in inspect.signature(fn).parameters}
try:
    fn(**call)
    print(json.dumps(["OK", 0]))
except Exception as e:
    frames = [t for t in traceback.extract_tb(e.__traceback__) if t.filename == path]
    print(json.dumps([type(e).__name__, frames[-1].lineno if frames else None]))
"""

EXC = {"division.zero_denominator": "ZeroDivisionError", "index.out_of_range": "IndexError"}

# (function, operation text) -> expected property_result: the precision the loop model must keep.
EXPECTED = {
    ("harmonic", "100 // i"): "PROVED_SAFE",
    ("down", "100 // i"): "PROVED_SAFE",
    ("down_to_zero", "100 // i"): "PROVED_UNSAFE",
    ("step_two_from_neg", "10 // i"): "PROVED_SAFE",
    ("step_three_hits_zero", "10 // i"): "PROVED_UNSAFE",
    ("computed_step", "10 // i"): "PROVED_UNSAFE",
    ("carried", "10 // d"): "PROVED_UNSAFE",
    ("while_count", "10 // (i - 2)"): "PROVED_UNSAFE",
    ("while_guard", "10 // n"): "PROVED_SAFE",
    ("while_true_break", "10 // i"): "PROVED_UNSAFE",
    ("for_else", "10 // d"): "PROVED_UNSAFE",
    ("continue_skip", "10 // i"): "PROVED_SAFE",
    ("nested", "10 // i"): "PROVED_SAFE",
    ("nested", "10 // (i - j)"): "PROVED_SAFE",
    ("nested_unsafe", "10 // (i - j)"): "PROVED_UNSAFE",
    ("len_index", "xs[i]"): "PROVED_SAFE",
    ("len_index_shrink", "xs[i]"): "UNKNOWN",
    ("iterate_list", "10 // c"): "PROVED_UNSAFE",
    ("iterate_str_literal", "10 // (c - 3)"): "PROVED_UNSAFE",
    ("return_in_loop", "10 // (i - 1)"): "PROVED_UNSAFE",
    ("maybe_unbound", "10 // last"): "UNKNOWN",
    ("type_change", "10 // v"): "UNKNOWN",
    ("tuple_target", "10 // b"): "UNKNOWN",
    ("unknown_iterable", "10 // k"): "POSSIBLY_UNSAFE",
    ("escape_by_break", "10 // d"): "POSSIBLY_UNSAFE",
    ("escape_by_continue", "10 // d"): "POSSIBLY_UNSAFE",
    ("long_loop", "10 // (s - 1000000)"): "POSSIBLY_UNSAFE",
    ("list_grows_while_iterated", "10 // (c - 3)"): "PROVED_UNSAFE",
}


def _child(script: str, *argv: str) -> object:
    p = subprocess.run([sys.executable, "-I", "-c", script, *argv], capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_loops_against_cpython(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()  # stop config discovery at this root
    path = root / "m.py"
    path.write_text(CORPUS + ADVERSARIAL, encoding="utf-8")
    code, rep, _ = check_json(root)
    assert rep["errors"] == [], rep["errors"]
    failing = {tuple(f) for f in _child(GROUND_TRUTH, str(path))}
    got: dict[tuple[str, str], str] = {}
    for o in rep["obligation_results"]:
        fn = o["symbol_id"].split("::")[1]
        if fn.startswith("_"):
            continue
        line, result = o["source_span"]["start_line"], o["property_result"]
        got[(fn, o["operation_text"])] = result
        exc = EXC[o["property_id"]]
        if result == "PROVED_SAFE":
            assert (fn, line, exc) not in failing, f"unsound PROVED_SAFE: CPython raises {exc} in {fn} at line {line}"
        if result == "PROVED_UNSAFE":
            assert o["witness_validated"] is True
            outcome = _child(REPLAY, str(path), fn, json.dumps(o["witness"] or {}))
            assert outcome == [exc, line], f"{fn}: witness {o['witness']} gives {outcome} in CPython, not {exc} at line {line}"
    assert {k: got.get(k) for k in EXPECTED} == EXPECTED
