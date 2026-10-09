"""Independent ground-truth oracle for the paper benchmarks.

It executes benchmark-authored fixture functions on hand-written inputs in a separate
interpreter process (``python -I``; ``-O`` when a case needs assertions disabled) that runs
on a temporary copy of the fixture tree. No InvariantGuard code is imported in the child or
used to judge the outcomes: the property checks below are plain Python, written for the
benchmark. A subprocess is process separation, not a sandbox; only fixtures written for this
repository are executed.

Outcomes are judged on the host:

* ``exception``: failure iff the call raised one of ``failure_exceptions``;
* ``sum_approx``: failure iff the call raised, or the result is not a list of finite numbers
  whose sum is within ``atol + rtol * |target|`` of ``target``;
* ``unchanged_argument``: failure iff the call raised or the argument changed (``==`` against a
  deep copy taken before the call);
* ``length_preserved``: failure iff the call raised or ``len(result) != len(xs)``;
* ``all_between``: failure iff the call raised or some element is outside ``[lower, upper]``.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

CHILD = r"""
import copy, importlib, json, sys
root = sys.argv[1]
sys.path.insert(0, root)
calls = json.loads(sys.stdin.read())

def enc(v, depth=0):
    if depth > 20:
        return {"opaque": "deep"}
    if v is None or isinstance(v, (bool, int, str)):
        if isinstance(v, int) and not isinstance(v, bool) and abs(v) > 2**63:
            return {"bigint": str(v)}
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else {"float": repr(v)}
    if isinstance(v, (list, tuple)):
        return {"seq": type(v).__name__, "items": [enc(x, depth + 1) for x in v]}
    return {"opaque": type(v).__name__}

out = []
for c in calls:
    rec = {}
    try:
        mod = importlib.import_module(c["module"])
        target = mod
        if c.get("construct") is not None:
            cls = getattr(mod, c["qualname"].split(".")[0])
            obj = cls(**c["construct"])
            fn = getattr(obj, c["qualname"].split(".")[1])
        else:
            fn = getattr(mod, c["qualname"])
        args = {k: (int(v["bigint"]) if isinstance(v, dict) and "bigint" in v else v) for k, v in c["args"].items()}
        before = copy.deepcopy(args)
        try:
            r = fn(**args)
            rec["kind"] = "returned"
            rec["value"] = enc(r)
        except BaseException as e:  # the property decides which exceptions are failures
            rec["kind"] = "raised"
            rec["exception"] = type(e).__name__
            rec["message"] = str(e)[:160]
            tb = e.__traceback__
            while tb is not None and tb.tb_next is not None:
                tb = tb.tb_next
            if tb is not None:
                rec["line"] = tb.tb_lineno
                rec["file"] = tb.tb_frame.f_code.co_filename.rsplit("/", 1)[-1]
        rec["args_unchanged"] = {k: args[k] == before[k] for k in args}
        rec["arg_lengths"] = {k: len(v) for k, v in args.items() if isinstance(v, list)}
    except BaseException as e:
        rec = {"kind": "harness_error", "exception": type(e).__name__, "message": str(e)[:300]}
    out.append(rec)
print(json.dumps(out))
"""


def _module_of(path: str) -> str:
    return path[:-3].replace("/", ".")


def execute(tree: Path, symbol: str, inputs: list[dict[str, Any]], *, optimize: bool = False, construct: dict | None = None,
            timeout: float = 120.0) -> list[dict[str, Any]]:
    """Run ``symbol`` (``path.py::qualname``) on each input in one fresh child interpreter."""
    if not inputs:
        return []
    path, qual = symbol.split("::")
    calls = [{"module": _module_of(path), "qualname": qual, "args": a, "construct": construct} for a in inputs]
    tmp = Path(tempfile.mkdtemp(prefix="ig-gt-"))
    try:
        work = tmp / "tree"
        shutil.copytree(tree, work)
        flags = ["-I", "-B"] + (["-O"] if optimize else [])
        p = subprocess.run([sys.executable, *flags, "-c", CHILD, str(work)], input=json.dumps(calls), capture_output=True, text=True,
                           timeout=timeout, cwd=tmp, env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0", "LANG": "C.UTF-8"})
        if p.returncode != 0:
            raise RuntimeError(f"oracle child failed ({p.returncode}): {p.stderr[-1500:]}")
        return json.loads(p.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _numbers(v: Any) -> list[float] | None:
    if isinstance(v, dict) and v.get("seq") == "list":
        xs = v["items"]
        if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in xs):
            return [float(x) for x in xs]
    return None


def precondition_holds(oracle: dict[str, Any], args: dict[str, Any]) -> bool:
    pre = oracle.get("precondition")
    if not pre:
        return True
    ws = args[oracle["param"]]
    base = len(ws) > 0 and all(0.0 <= w <= 100.0 for w in ws)
    if pre == "positive_sum_0_100":
        return base and sum(ws) > 0
    if pre == "nonneg_0_100":
        return base
    raise ValueError(pre)


def fails(oracle: dict[str, Any], outcome: dict[str, Any], args: dict[str, Any]) -> bool | None:
    """True = property violated, False = satisfied, None = harness error (no label)."""
    if outcome["kind"] == "harness_error":
        return None
    kind = oracle["kind"]
    if kind == "exception":
        return outcome["kind"] == "raised" and outcome["exception"] in oracle["failure_exceptions"]
    if outcome["kind"] == "raised":
        return True
    v = outcome["value"]
    if kind == "sum_approx":
        xs = _numbers(v)
        if xs is None or not all(math.isfinite(x) for x in xs):
            return True
        return abs(sum(xs) - oracle["target"]) > oracle["atol"] + oracle["rtol"] * abs(oracle["target"])
    if kind == "unchanged_argument":
        return not outcome["args_unchanged"][oracle["param"]]
    if kind == "length_preserved":
        if not (isinstance(v, dict) and "items" in v):
            return True
        return len(v["items"]) != len(args[oracle.get("param", "xs")])
    if kind == "all_between":
        xs = _numbers(v)
        return xs is None or any(not (oracle["lower"] <= x <= oracle["upper"]) for x in xs)
    raise ValueError(kind)


def compare(base_tree: Path, patch_tree: Path, symbol: str, oracle: dict[str, Any], inputs: list[dict[str, Any]]) -> dict[str, Any]:
    """Execute every admitted input on both versions and classify it."""
    admitted = [a for a in inputs if precondition_holds(oracle, a)]
    b = execute(base_tree, symbol, admitted)
    p = execute(patch_tree, symbol, admitted)
    rows = []
    for a, ob, op in zip(admitted, b, p):
        fb, fp = fails(oracle, ob, a), fails(oracle, op, a)
        cls = ("undetermined" if fb is None or fp is None else "introduced" if (not fb and fp) else "pre_existing" if (fb and fp)
               else "resolved" if (fb and not fp) else "ok")
        rows.append({"input": a, "base": ob, "patch": op, "base_fails": fb, "patch_fails": fp, "class": cls})
    return {"admitted": len(admitted), "excluded_by_precondition": len(inputs) - len(admitted), "rows": rows,
            "introduced": sum(r["class"] == "introduced" for r in rows), "pre_existing": sum(r["class"] == "pre_existing" for r in rows),
            "resolved": sum(r["class"] == "resolved" for r in rows), "undetermined": sum(r["class"] == "undetermined" for r in rows)}


if __name__ == "__main__":  # tiny manual entry point: python benchmarks/gt_oracle.py TREE SYMBOL '{"a": 1}'
    print(json.dumps(execute(Path(sys.argv[1]), sys.argv[2], [json.loads(sys.argv[3])]), indent=1))
