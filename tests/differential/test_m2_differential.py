"""Seeded random differential test for the M2 analyses (T22, T25).

Generated functions use declared exact-int parameters ``a, b``, a declared exact built-in
list ``xs``, a declared optional int ``o``, list/tuple literals, ``len`` and truthiness
guards, constant and computed indexes, aliases mutated through unknown calls, ``is None``
guards, and direct calls to module helpers (call summaries) that return ``None`` on some
paths, divide, or index. ``guard check`` analyzes the generated module; every claim is
then checked against isolated CPython execution of that generated fixture:

* PROVED_SAFE: no grid input raises the property's exception (ZeroDivisionError,
  IndexError, AttributeError) at that exact source span;
* PROVED_UNSAFE: the reported witness raises that exception at that exact span.
"""

from __future__ import annotations

import itertools
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from ..conftest import check_json, write_project

RUNNER = r"""
import json, sys, traceback, runpy
req = json.loads(sys.stdin.read())
ns = runpy.run_path(req["path"])
out = {}
for name, calls in req["calls"].items():
    fn = ns[name]
    res = []
    for kw in calls:
        try:
            fn(**kw)
            res.append(None)
        except (ZeroDivisionError, IndexError, AttributeError) as e:
            frames = traceback.extract_tb(e.__traceback__)[1:]  # drop this runner's frame
            res.append([type(e).__name__] + [[fs.lineno, fs.colno, fs.end_lineno, fs.end_colno] for fs in frames])
        except Exception as e:
            res.append(type(e).__name__)
    out[name] = res
print(json.dumps(out))
"""

EXC = {"division.zero_denominator": "ZeroDivisionError", "index.out_of_range": "IndexError", "none.attribute_access": "AttributeError"}
GRID = [dict(a=a, b=b, xs=xs, o=o) for a, b, xs, o in itertools.product(range(-3, 4), range(-3, 4), ([], [0], [0, 0], [0, 0, 0]), (None, 0, 1))]

HELPERS = """
K = 2


def h_shift(x):
    return x - K


def h_maybe(x):
    if x > 0:
        return x
    return None


def h_pick(xs, i):
    return xs[i]


def h_inv(d):
    return 12 // d


def h_two(x, k=1):
    return x % k


def g(*args):
    return 0
"""


class Gen:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    def idx(self) -> str:
        return self.rng.choice(["a", "b", "0", "-1", "1", "2", "-3", "n - 1", "n", "a % 3", "-n", "b - a"])

    def stmt(self, ind: str, lines: list[str], depth: int) -> None:
        rng = self.rng
        r = rng.random()
        if r < 0.15:
            lines.append(f"{ind}t = xs[{self.idx()}]")
        elif r < 0.25:
            lines.append(f"{ind}t = ys[{self.idx()}]")
        elif r < 0.33:
            lines.append(f"{ind}t = h_pick(xs, {self.idx()})")
        elif r < 0.42:
            if rng.random() < 0.5:
                lines.append(f"{ind}if v is not None:")
                lines.append(f"{ind}    t = v.bit_length()")
            else:
                lines.append(f"{ind}t = v.bit_length()")
        elif r < 0.48:
            if rng.random() < 0.5:
                lines.append(f"{ind}if o is None:")
                lines.append(f"{ind}    return 1")
            lines.append(f"{ind}t = o.bit_length()")
        elif r < 0.55:
            lines.append(f"{ind}t = 100 // h_shift({rng.choice(['a', 'b', 'n'])})")
        elif r < 0.6:
            lines.append(f"{ind}t = h_inv({rng.choice(['a', 'b - 1', 'n', 'len(ys) - 3'])})")
        elif r < 0.62:
            # Keyword arguments evaluate in call-site order, not parameter order.
            lines.append(f"{ind}t = h_two(k={rng.choice(['a', 'b', 'n', '1'])}, x=100 // {rng.choice(['a', 'b', '(a - b)', 'n'])})")
        elif r < 0.65:
            lines.append(f"{ind}g(zs)")  # zs aliases xs and escapes into unknown code
        elif r < 0.68:
            lines.append(f"{ind}v = h_maybe({rng.choice(['a', 'b', 'n'])})")
        elif depth < 2:
            cond = rng.choice([f"len(xs) > {rng.randint(0, 2)}", "xs", f"n >= {rng.randint(1, 3)}", "v is not None", "o is not None",
                               "v is None", f"a > {rng.randint(-1, 2)}", f"0 <= b < n", f"-n <= a < n"])
            lines.append(f"{ind}if {cond}:")
            for _ in range(rng.randint(1, 2)):
                self.stmt(ind + "    ", lines, depth + 1)
            if rng.random() < 0.3:
                lines.append(f"{ind}    return 0")
        else:
            lines.append(f"{ind}t = xs[{self.idx()}]")

    def function(self, i: int) -> str:
        lines = [f"def f{i}(a, b, xs, o):", "    n = len(xs)", "    ys = [a, b, 1]", "    zs = xs", "    v = h_maybe(a)"]
        for _ in range(self.rng.randint(2, 4)):
            self.stmt("    ", lines, 0)
        lines.append("    return 0")
        return "\n".join(lines) + "\n"


def _replay(path: Path, calls: dict[str, list[dict]]) -> dict[str, list]:
    p = subprocess.run([sys.executable, "-I", "-c", RUNNER], input=json.dumps({"path": str(path), "calls": calls}),
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _config(n: int) -> str:
    lines = ['schema_version = "1.0"', "[analysis]", "quick_budget_ms = 60000", "per_query_timeout_ms = 20000", "per_query_rlimit = 10000000"]
    for i in range(n):
        for params, prof in ((["a", "b"], "python_exact_builtin_ints"), (["xs"], "python_exact_builtin_list"), (["o"], "python_optional_exact_builtin_int")):
            lines += ["[[domains]]", f'symbol = "m.py::f{i}"', f'profile = "{prof}"', 'authority = "DECLARED"', 'reason = "generated fixture"',
                      "params = " + json.dumps(params)]
    return "\n".join(lines) + "\n"


_EXTRA = [int(x) for x in __import__("os").environ.get("IG_M2_DIFF_SEEDS", "").split(",") if x.strip()]
SEEDS = [(7, False), (8, False), (9, True)] + [(s, ns) for s in _EXTRA for ns in (False, True)]


@pytest.mark.parametrize("seed,no_solver", SEEDS)
def test_random_m2_programs_agree_with_cpython(tmp_path, seed, no_solver):
    n = 30
    gen = Gen(random.Random(seed))
    src = HELPERS + "\n\n" + "\n\n".join(gen.function(i) for i in range(n))
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()
    write_project(root, {"m.py": src}, _config(n))
    _, report, _ = check_json(root, *(["--no-solver"] if no_solver else []))
    assert report["errors"] == [], report["errors"]
    path = root / "m.py"
    observed = _replay(path, {f"f{i}": GRID for i in range(n)})

    counts: dict[tuple[str, str], int] = {}
    problems = []
    checks = []
    for o in report["obligation_results"]:
        fn = o["symbol_id"].split("::", 1)[1]
        if not fn.startswith("f"):
            continue
        prop = o["property_id"].removesuffix(".via_call")
        counts[(prop, o["property_result"])] = counts.get((prop, o["property_result"]), 0) + 1
        # Expected traceback: the call sites of the inlined chain (via_call), then the operation.
        spans = [c["call_span"] for c in o.get("via", [])] + [o["source_span"]]
        key = [EXC[prop]] + [[sp["start_line"], sp["start_column"], sp["end_line"], sp["end_column"]] for sp in spans]
        if o["property_result"] == "PROVED_SAFE":
            hits = [GRID[k] for k, r in enumerate(observed[fn]) if r == key]
            if hits:
                problems.append(("false PROVED_SAFE", o["obligation_id"], o["operation_text"], hits[:3]))
        elif o["property_result"] == "PROVED_UNSAFE":
            w = dict(a=0, b=0, xs=[], o=None)
            w.update(o["witness"])
            checks.append((fn, w, key, o["obligation_id"]))
    by_fn: dict[str, list] = {}
    for fn, w, key, oid in checks:
        by_fn.setdefault(fn, []).append(w)
    got = _replay(path, by_fn) if by_fn else {}
    seen: dict[str, int] = {}
    for fn, w, key, oid in checks:
        i = seen.get(fn, 0)
        seen[fn] = i + 1
        if got[fn][i] != key:
            problems.append(("false PROVED_UNSAFE", oid, w, got[fn][i], key))
    assert problems == [], problems
    # Non-vacuity: every M2 property is exercised in both proof directions across the corpus.
    for prop in ("index.out_of_range", "none.attribute_access", "division.zero_denominator"):
        assert counts.get((prop, "PROVED_SAFE"), 0) >= 2 and counts.get((prop, "PROVED_UNSAFE"), 0) >= 2, (prop, counts)
    assert counts.get(("index.out_of_range", "UNKNOWN"), 0) >= 1  # aliased list mutated by unknown code
