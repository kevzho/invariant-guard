"""Seeded random differential test (audit, M1).

Generates small Python functions in the M1 subset (declared exact-int parameters, a
bool-annotated parameter, branches, chained/negated/short-circuit conditions, augmented
assignments, floor division/modulo with signed constants, unary minus, asserts,
maybe-unbound locals and unknown calls), analyzes them with the real ``guard check``
pipeline, and checks every claim against isolated CPython execution of the generated
test fixture (never repository code):

* PROVED_SAFE: no input on the grid raises ZeroDivisionError at that exact span, with
  assertions enabled and under ``python -O``.
* PROVED_UNSAFE: the reported witness raises ZeroDivisionError at that exact span in the
  reported runtime mode.

The grid is finite evidence for the proofs; the witness check is exact. This test found
audit findings A-01 (solver name aliasing) and A-03 (unbound-read semantics).
"""

from __future__ import annotations

import itertools
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from ..conftest import check_json, domain_config, write_project

RUNNER = r"""
import json, sys, traceback, runpy
req = json.loads(sys.stdin.read())
ns = runpy.run_path(req["path"])
out = {}
for name, calls in req["calls"].items():
    fn = ns[name]
    res = []
    for args in calls:
        try:
            fn(*args)
            res.append(None)
        except ZeroDivisionError as e:
            fs = traceback.extract_tb(e.__traceback__)[-1]
            res.append([fs.lineno, fs.colno, fs.end_lineno, fs.end_colno])
        except Exception as e:
            res.append(type(e).__name__)
    out[name] = res
print(json.dumps(out))
"""

GRID = [list(t) for t in itertools.product(range(-4, 5), range(-4, 5), (False, True))]


class Gen:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    def atom(self, vs: list[str]) -> str:
        r = self.rng.random()
        if r < 0.1:
            return "c"  # bool parameter used as an int
        if r < 0.15:
            return "g(a)"  # unknown call (result and effects unmodeled)
        return self.rng.choice(vs + [str(self.rng.randint(-3, 3))])

    def expr(self, vs: list[str], depth: int = 0) -> str:
        if depth > 2 or self.rng.random() < 0.3:
            return self.atom(vs)
        op = self.rng.choice(["+", "-", "*", "//", "%", "+", "-"])
        if op in ("//", "%"):
            rhs = self.rng.choice([str(self.rng.choice([-3, -2, -1, 1, 2, 3])), self.rng.choice(vs)])
        else:
            rhs = self.expr(vs, depth + 1)
        e = f"({self.expr(vs, depth + 1)} {op} {rhs})"
        return f"-{e}" if self.rng.random() < 0.1 else e

    def divisor(self, vs: list[str]) -> str:
        """Divisors that are zero exactly at a branch boundary stress refinement."""
        r = self.rng.random()
        if r < 0.5:
            return f"({self.rng.choice(vs)} - {self.rng.randint(-3, 3)})"
        return self.expr(vs)

    def cond(self, vs: list[str]) -> str:
        rng = self.rng
        cmp = rng.choice(['==', '!=', '<', '<=', '>', '>='])
        if rng.random() < 0.45:
            # Direct variable/constant comparison (in either order): refinement applies.
            v, k = rng.choice(vs), rng.randint(-3, 3)
            c = f"{v} {cmp} {k}" if rng.random() < 0.7 else f"{k} {cmp} {v}"
        else:
            c = f"{self.expr(vs, 1)} {cmp} {self.expr(vs, 1)}"
        r = rng.random()
        if r < 0.15:
            return f"not ({c})"
        if r < 0.3:
            return f"{c} {rng.choice(['and', 'or'])} {rng.choice(vs)} {rng.choice(['==', '!=', '<', '>'])} {rng.randint(-2, 2)}"
        if r < 0.4:
            return f"{rng.randint(-2, 0)} < {rng.choice(vs)} <= {rng.randint(0, 3)}"
        if r < 0.5:
            return rng.choice(vs + ["c"])
        return c

    def block(self, vs: list[str], ind: str, depth: int, lines: list[str]) -> None:
        rng = self.rng
        for _ in range(rng.randint(1, 3)):
            r = rng.random()
            if r < 0.4:
                v = rng.choice(["d", "e", "a", "b"])
                lines.append(f"{ind}{v} = {self.expr(vs)}")
                if v not in vs:
                    vs.append(v)
            elif r < 0.5:
                lines.append(f"{ind}{rng.choice(vs)} {rng.choice(['+=', '-=', '*=', '//=', '%='])} {self.expr(vs, 1)}")
            elif r < 0.57:
                lines.append(f"{ind}assert {self.cond(vs)}")
            elif r < 0.85 and depth < 2:
                lines.append(f"{ind}if {self.cond(vs)}:")
                then_vs = list(vs)
                self.block(then_vs, ind + "    ", depth + 1, lines)
                if rng.random() < 0.4:
                    lines.append(f"{ind}    return {self.expr(then_vs)} {rng.choice(['/', '//', '%'])} {self.divisor(then_vs)}")
                if rng.random() < 0.5:
                    lines.append(f"{ind}else:")
                    else_vs = list(vs)
                    self.block(else_vs, ind + "    ", depth + 1, lines)
                    vs[:] = [x for x in then_vs if x in else_vs]
                # Otherwise names bound only in the then-branch stay maybe-unbound; reading
                # them (below) exercises UnboundLocalError semantics.
                if rng.random() < 0.2:
                    maybe = [x for x in then_vs if x not in vs]
                    if maybe:
                        lines.append(f"{ind}{rng.choice(['a', 'b'])} = {maybe[0]} + 0")
            else:
                lines.append(f"{ind}if {self.cond(vs)}:")
                lines.append(f"{ind}    return 0")

    def function(self, i: int) -> str:
        vs = ["a", "b"]
        lines = [f"def f{i}(a, b, c: bool):"]
        self.block(vs, "    ", 0, lines)
        lines.append(f"    return {self.expr(vs)} {self.rng.choice(['/', '//', '%'])} {self.divisor(vs)}")
        return "\n".join(lines) + "\n"


def _replay(path: Path, calls: dict[str, list[list]], optimize: bool) -> dict[str, list]:
    cmd = [sys.executable, "-I"] + (["-O"] if optimize else []) + ["-c", RUNNER]
    p = subprocess.run(cmd, input=json.dumps({"path": str(path), "calls": calls}), capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.parametrize("seed,no_solver", [(101, False), (202, False), (303, True)])
def test_random_programs_agree_with_cpython(tmp_path, seed, no_solver):
    n = 40
    gen = Gen(random.Random(seed))
    src = "\n".join(gen.function(i) for i in range(n)) + "\n\ndef g(*args):\n    return 0\n"
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()
    decls = [f"m.py::f{i}" for i in range(n)]
    config = domain_config(*decls).replace('reason = "test fixture"', 'reason = "test fixture"\nparams = ["a", "b"]')
    write_project(root, {"m.py": src}, config)
    _, report, _ = check_json(root, *(["--no-solver"] if no_solver else []))
    assert report["errors"] == [], report["errors"]

    path = root / "m.py"
    calls = {f"f{i}": GRID for i in range(n)}
    observed = {False: _replay(path, calls, False), True: _replay(path, calls, True)}

    counts: dict[str, int] = {}
    problems = []
    unsafe_calls: dict[tuple[str, bool], list] = {}
    unsafe_keys = []
    for o in report["obligation_results"]:
        counts[o["property_result"]] = counts.get(o["property_result"], 0) + 1
        fn = o["symbol_id"].split("::", 1)[1]
        sp = o["source_span"]
        key = [sp["start_line"], sp["start_column"], sp["end_line"], sp["end_column"]]
        if o["property_result"] == "PROVED_SAFE":
            for opt in (False, True):
                hits = [GRID[k] for k, r in enumerate(observed[opt][fn]) if r == key]
                if hits:
                    problems.append(("false PROVED_SAFE", o["obligation_id"], o["operation_text"], "-O" if opt else "", hits[:3]))
        elif o["property_result"] == "PROVED_UNSAFE":
            w = o["witness"]
            assert o["witness_validated"] is True
            opt = o["runtime_mode"] == "python -O (assertions disabled)"
            args = [w.get("a", 0), w.get("b", 0), w.get("c", False)]
            unsafe_calls.setdefault((fn, opt), []).append(args)
            unsafe_keys.append((fn, opt, args, key, o["obligation_id"]))
    replays = {}
    for opt in (False, True):
        sub = {fn: c for (fn, o2), c in unsafe_calls.items() if o2 == opt}
        if sub:
            replays[opt] = _replay(path, sub, opt)
    seen_idx: dict[tuple[str, bool], int] = {}
    for fn, opt, args, key, oid in unsafe_keys:
        i = seen_idx.get((fn, opt), 0)
        seen_idx[(fn, opt)] = i + 1
        got = replays[opt][fn][i]
        if got != key:
            problems.append(("false PROVED_UNSAFE", oid, args, "-O" if opt else "", got))
    assert problems == [], problems
    # Non-vacuity: the generated corpus must exercise both proof directions.
    assert counts.get("PROVED_SAFE", 0) >= 50 and counts.get("PROVED_UNSAFE", 0) >= 20, counts
