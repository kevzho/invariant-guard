"""Declared and approved behavior in differential replay (pivot phase 5): doctests and pins.

A behavior difference alone never blocks (``evidence.py``). Two sources of intent can:

* **Doctests** are DECLARED behavior: the repository states the expected output. Every
  doctest example in the modules a patch changed runs inside the boundary on the base
  (twice) and the candidate (twice). An example that is identical on both sides (same
  owner, source and expected output), passes in both base runs and fails in both
  candidate runs is an OBSERVED_REGRESSION (DECLARED, INTRODUCED, ERROR: blocks). An
  example the patch edited or added is the patch stating new intent: not a regression.
  An example that fails on the base too is not reported (pre-existing, or it needs a
  setup the runner does not have); base runs that disagree are nondeterministic and
  ignored.
* **Pins** are APPROVED behavior (``guard behavior pin``): a recorded literal input with
  its expected outcome. Each active pin runs on the base once and the candidate twice; a
  candidate outcome that differs from the pin (on the observable view) while the base
  matches is an OBSERVED_REGRESSION (APPROVED, INTRODUCED, ERROR); if the base differs
  too it is PRE_EXISTING (WARNING). A pin that cannot be executed is required work left
  undone (INCOMPLETE).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..execution.runner import Backend, Workspace
from . import compare
from .decisions import Decision
from .run import Construct, Probe, Target, Value, run_replay


@dataclass
class DeclaredResult:
    regressions: list[dict[str, Any]] = field(default_factory=list)  # one per regressed doctest example or pin
    stats: dict[str, Any] = field(default_factory=dict)
    incomplete: list[str] = field(default_factory=list)


def _module_targets(paths: list[str], python_path: list[str]) -> dict[str, dict[str, str]]:
    from .engine import _root_for

    out = {}
    for i, p in enumerate(sorted(set(paths))):
        root = _root_for(p, python_path)
        try:
            mod = Target(p, "_", root).module
        except ValueError:
            continue
        out[f"m{i}"] = {"module": mod, "path": p, "root": root}
    return out


def _run_doctests(backend: Backend, files: dict[str, bytes], mods: dict[str, dict[str, str]], root: str) -> dict[str, Any]:
    prof = backend.profile
    req = {"mode": "doctest", "src_root": "", "python_path_root": root, "site_packages": prof.site_packages,
           "targets": {k: {"module": v["module"], "qualname": "<module>"} for k, v in mods.items() if v["root"] == root},
           "probe_wall_seconds": max(5.0, prof.probe_wall_seconds * 5), "probe_cpu_seconds": max(1, int(min(prof.cpu_seconds, prof.probe_wall_seconds * 10)))}
    ws = Workspace(files, "doctest")
    try:
        req["src_root"] = backend.src_root(ws)
        r = backend.run(ws, req, harness="replay")
    finally:
        ws.close()
    if r.response is None:
        return {"error": r.error}
    res = r.response.get("results")
    return res if isinstance(res, dict) else {"error": "no results"}


def _examples(res: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    """(test name, source, want) -> {"outcome": pass|fail|error, "got": ...}; malformed entries are dropped."""
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for tid, v in res.items():
        if not isinstance(v, dict) or v.get("status") != "ok" or not isinstance(v.get("tests"), list):
            continue
        for t in v["tests"]:
            if not isinstance(t, dict) or not isinstance(t.get("examples"), list):
                continue
            seen: dict[tuple[str, str, str], int] = {}
            for e in t["examples"]:
                if not isinstance(e, dict) or not all(isinstance(e.get(k), str) for k in ("source", "want", "outcome")):
                    continue
                base_key = (str(t.get("name")), e["source"], e["want"])
                n = seen.get(base_key, 0)  # the same example repeated in one docstring keeps its order
                seen[base_key] = n + 1
                out[(base_key[0], base_key[1], base_key[2] + ("" if n == 0 else f"\x00#{n}"))] = {"outcome": e["outcome"], "got": str(e.get("got", ""))[:2000]}
    return out


def check_doctests(backend: Backend, base_files: dict[str, bytes], cand_files: dict[str, bytes], changed_paths: list[str],
                   python_path: list[str]) -> DeclaredResult:
    out = DeclaredResult()
    mods = _module_targets([p for p in changed_paths if p in base_files and p in cand_files], python_path)
    if not mods:
        return out
    b1: dict[str, Any] = {}
    b2: dict[str, Any] = {}
    c1: dict[str, Any] = {}
    c2: dict[str, Any] = {}
    errors = []
    for root in sorted({v["root"] for v in mods.values()}):
        for side, files, acc in (("base", base_files, b1), ("base_repeat", base_files, b2), ("candidate", cand_files, c1), ("candidate_repeat", cand_files, c2)):
            r = _run_doctests(backend, files, mods, root)
            if "error" in r and len(r) == 1:
                errors.append(f"{side}: {r['error']}")
                continue
            acc.update(r)
    eb1, eb2, ec1, ec2 = _examples(b1), _examples(b2), _examples(c1), _examples(c2)
    stats = {"modules": len(mods), "examples_base": len(eb1), "examples_candidate": len(ec1), "unchanged": 0, "nondeterministic": 0,
             "failing_on_base": 0, "regressions": 0, "resolved": 0, "errors": errors[:5]}
    name_of = {v["module"]: v["path"] for v in mods.values()}
    for key in sorted(set(eb1) & set(ec1)):
        stats["unchanged"] += 1
        a, a2, c, cc = eb1[key], eb2.get(key), ec1[key], ec2.get(key)
        if a2 is None or a2["outcome"] != a["outcome"] or a2["got"] != a["got"]:
            stats["nondeterministic"] += 1
            continue
        if a["outcome"] != "pass":
            stats["failing_on_base"] += 1
            if c["outcome"] == "pass" and cc is not None and cc["outcome"] == "pass":
                stats["resolved"] += 1
            continue
        if c["outcome"] != "pass" and cc is not None and cc["outcome"] == c["outcome"] and cc["got"] == c["got"]:
            stats["regressions"] += 1
            test_name, source, want = key
            module = next((m for m in name_of if test_name == m or test_name.startswith(m + ".")), None)
            path = name_of.get(module or "", "")
            owner = test_name[len(module) + 1:] if module and test_name != module else "<module>"
            out.regressions.append({"kind": "doctest", "path": path, "owner": owner, "test": test_name, "source": source,
                                    "want": want.split("\x00#")[0], "got_base": a["got"], "got_patch": c["got"], "outcome_patch": c["outcome"]})
    out.stats = stats
    return out


def _probe_from_spec(pid: str, target: Target, spec: dict[str, Any]) -> Probe:
    args = tuple(Value(a["v"]) for a in spec.get("args", []))
    kwargs = tuple((k, Value(a["v"])) for k, a in spec.get("kwargs", {}).items())
    recv = None
    if spec.get("receiver"):
        c = spec["receiver"]["construct"]
        recv = Construct(tuple(Value(a["v"]) for a in c.get("args", [])), tuple((k, Value(a["v"])) for k, a in c.get("kwargs", {}).items()))
    return Probe(pid, target, args, kwargs, recv, int(spec.get("seed", 0)))


def check_pins(backend: Backend, base_files: dict[str, bytes], cand_files: dict[str, bytes], pins: list[Decision], python_path: list[str]) -> DeclaredResult:
    from .engine import _root_for

    out = DeclaredResult()
    probes: list[tuple[Decision, Probe]] = []
    for d in pins:
        tsym = d.record["target"]
        path, qual = tsym.split("::", 1)
        if path not in cand_files:
            out.incomplete.append(f"pinned behavior {d.decision_id} ({tsym}): the target module is not in the candidate")
            continue
        probes.append((d, _probe_from_spec(f"pin{len(probes)}", Target(path, qual, _root_for(path, python_path)), d.record["probe"])))
    if not probes:
        out.stats = {"pins": len(pins), "checked": 0}
        return out
    ps = [p for _, p in probes]
    prov = {**base_files, **cand_files}
    b = run_replay(backend, base_files, ps, provenance=prov).observations
    c1 = run_replay(backend, cand_files, ps, provenance=prov).observations
    c2 = run_replay(backend, cand_files, ps, provenance=prov).observations
    passed = 0
    for d, p in probes:
        exp = compare.canon(d.record["expected"])
        ob, o1, o2 = b.get(p.id), c1.get(p.id), c2.get(p.id)
        if o1 is None or o1.get("status") not in ("ok", "timeout") or o2 is None or compare.canon(o1) != compare.canon(o2):
            out.incomplete.append(f"pinned behavior {d.decision_id} ({d.record['call']}) could not be checked on the candidate "
                                  f"({compare.render_outcome(o1)})")
            continue
        if compare.canon(o1) == exp:
            passed += 1
            continue
        base_ok = ob is not None and compare.canon(ob) == exp
        out.regressions.append({"kind": "pin", "decision_id": d.decision_id, "target": d.record["target"], "changed_symbol": d.record["changed_symbol"],
                                "call": d.record["call"], "expected": d.record["expected_text"], "got_base": compare.render_outcome(ob),
                                "got_patch": compare.render_outcome(o1), "relation": "INTRODUCED" if base_ok else "PRE_EXISTING",
                                "reason": d.reason, "created_at": d.created_at})
    out.stats = {"pins": len(pins), "checked": len(probes), "passed": passed, "violated": len(out.regressions)}
    return out
