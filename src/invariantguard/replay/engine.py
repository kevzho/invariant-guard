"""Differential replay of one patch: identical inputs on base and patch, confirmed differences.

``replay_patch`` takes the base and candidate source sets (all captured ``.py`` and
``.pyi`` files, tests included), the symbols the patch changed, and a runner backend:

1. Targets. A changed function or method is replayed directly; a changed ``__init__`` /
   ``__new__`` through its class; a symbol that only reads changed module data likewise.
   Symbols that cannot be addressed by import (nested functions, lambdas) are coverage
   gaps. A target without harvested inputs, or a private one, is also observed through up
   to ``max_callers`` same-module callers.
2. Inputs. Harvested from the candidate's and the base's tests and doctests (a call text
   only the candidate contains is *acknowledged by the patch*), default perturbations,
   and optionally in-boundary Hypothesis generation (profile ``site_packages``).
3. Runs. Base twice, candidate once, each in its own fresh boundary run. Every probe is
   classified on the observable view (``compare``). Differences are re-run (base once,
   candidate twice, fresh runs) and only reproduced ones are reported.
4. Coverage. Every changed public symbol needs at least one executed input on both
   sides (directly, or through its class or a caller); otherwise it is a required gap.

Nothing here decides a verdict: ``evidence.py`` maps the result to findings and policy.
"""

from __future__ import annotations

import ast
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..execution.runner import Backend
from . import compare
from .generate import plan, run_generation
from .harvest import Harvested, default_variants, harvest, target_kind
from .run import Probe, ReplayRun, Target, run_replay

MAX_CALLERS = 3
EXECUTED = ("same", "internal_state_only", "nondeterministic", "unconfirmed", "timeout_both", "timeout") + compare.DIFF_CLASSES


@dataclass
class TargetPlan:
    symbol: str  # changed symbol id (candidate)
    target: Target
    relation: str  # direct | constructor | caller | data_reader
    required: bool
    kind: str
    inputs: list[Harvested] = field(default_factory=list)
    acknowledged: set[str] = field(default_factory=set)  # input keys only the candidate's tests/doctests contain
    harvest_stats: dict[str, int] = field(default_factory=dict)
    gen_stats: dict[str, Any] = field(default_factory=dict)
    classes: dict[str, int] = field(default_factory=dict)

    @property
    def executed(self) -> bool:
        return any(self.classes.get(c, 0) for c in EXECUTED)

    def to_json(self) -> dict[str, Any]:
        origins: dict[str, int] = {}
        for h in self.inputs:
            origins[h.origin] = origins.get(h.origin, 0) + 1
        return {"symbol_id": self.symbol, "target": self.target.symbol_id, "relation": self.relation, "kind": self.kind, "required": self.required,
                "inputs": len(self.inputs), "inputs_by_origin": dict(sorted(origins.items())), "acknowledged_inputs": len(self.acknowledged),
                "executed": self.executed, "classes": dict(sorted(self.classes.items())), "harvest": self.harvest_stats, "generation": self.gen_stats}


@dataclass
class ReplayResult:
    targets: list[TargetPlan] = field(default_factory=list)
    clusters: list[compare.Cluster] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)  # changed symbols with no executed input
    runs: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    timing_ms: dict[str, float] = field(default_factory=dict)
    probes: int = 0
    unconfirmed: int = 0

    def coverage(self) -> dict[str, Any]:
        req = {t.symbol for t in self.targets if t.required}
        return {"changed_symbols": sorted({t.symbol for t in self.targets} | {g["symbol_id"] for g in self.gaps}),
                "required_symbols": sorted(req | {g["symbol_id"] for g in self.gaps if g["required"]}),
                "required_gaps": [g for g in self.gaps if g["required"]], "advisory_gaps": [g for g in self.gaps if not g["required"]]}


def is_public(qualname: str) -> bool:
    return not any(p.startswith("_") and not (p.startswith("__") and p.endswith("__")) for p in qualname.split("."))


def _root_for(path: str, python_path: list[str]) -> str:
    for r in python_path:
        r = r.strip("/")
        if r not in ("", ".") and path.startswith(r + "/"):
            return r
    return "."


def same_module_callers(src: bytes, qualname: str, limit: int = MAX_CALLERS) -> list[str]:
    """Module-level functions and methods whose body mentions the changed member's name."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    name = qualname.split(".")[-1]
    if name in ("__init__", "__new__"):
        name = qualname.split(".")[0]
    out: list[str] = []
    defs: list[tuple[str, ast.AST]] = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.append((n.name, n))
        elif isinstance(n, ast.ClassDef):
            defs += [(f"{n.name}.{m.name}", m) for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for q, node in defs:
        if q == qualname or isinstance(node, ast.AsyncFunctionDef):
            continue
        if any((isinstance(x, ast.Name) and x.id == name) or (isinstance(x, ast.Attribute) and x.attr == name) for x in ast.walk(node)):
            out.append(q)
    out.sort(key=lambda q: (not is_public(q), q))
    return out[:limit]


def test_paths(files: dict[str, bytes]) -> list[str]:
    return sorted(p for p in files if p.endswith(".py") and ("tests/" in p or "/test/" in p or p.split("/")[-1].startswith("test_")
                                                             or p.startswith("test")))


def changed_for_replay(modified: list[str], reasons: dict[str, str], symbols: set[str]) -> list[tuple[str, str]]:
    """Replay targets from static change detection: modified symbols, plus direct readers of a changed
    module binding that is data (a binding naming a function or class is a code change: that symbol is
    itself a target, and its callers are reached through it)."""
    mods = [c for c in modified if c in symbols]
    data: list[str] = []
    for c, why in sorted(reasons.items()):
        if c in mods or c not in symbols or not (why.startswith("reads ") and why.endswith("(binding changed)")):
            continue
        name = why[len("reads "):-len(" (binding changed)")]  # "<module path>::<name>"
        if name in symbols or any(s.startswith(name + ".") for s in symbols):
            continue
        data.append(c)
    return [(c, "modified") for c in mods] + [(c, "data") for c in data]


def plan_targets(changed: list[tuple[str, str]], cand_files: dict[str, bytes], python_path: list[str], max_callers: int) -> tuple[list[TargetPlan], list[dict[str, Any]]]:
    """``changed``: (symbol id, how) with how in modified | data. Returns plans and non-addressable gaps."""
    plans: list[TargetPlan] = []
    gaps: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    trees: dict[str, ast.Module | None] = {}
    for sym, how in changed:
        path, qual = sym.split("::", 1)
        required = is_public(qual)
        if not path.endswith(".py") or path in test_paths({path: b""}):
            continue
        if any(x in qual for x in ("<locals>", "<lambda>", "#", "<module>")):
            gaps.append({"symbol_id": sym, "required": False, "reason": "not addressable by import (nested, lambda or module code)"})
            continue
        if path not in trees:
            try:
                trees[path] = ast.parse(cand_files[path])
            except (KeyError, SyntaxError):
                trees[path] = None
        tree = trees[path]
        if tree is None:
            gaps.append({"symbol_id": sym, "required": required, "reason": "module could not be parsed"})
            continue
        parts = qual.split(".")
        root = _root_for(path, python_path)
        relation = "data_reader" if how == "data" else "direct"
        tq = qual
        if parts[-1] in ("__init__", "__new__") and len(parts) == 2:
            tq, relation = parts[0], "constructor"
        kind = target_kind(tree, tq)
        if kind is None:
            gaps.append({"symbol_id": sym, "required": required, "reason": "definition not found at module or class level"})
            continue
        own: list[TargetPlan] = []
        if (sym, tq) not in seen:
            seen.add((sym, tq))
            own.append(TargetPlan(sym, Target(path, tq, root), relation, required, kind))
        for cq in same_module_callers(cand_files[path], qual, max_callers):
            ck = target_kind(tree, cq)
            if ck is not None and (sym, cq) not in seen:
                seen.add((sym, cq))
                own.append(TargetPlan(sym, Target(path, cq, root), "caller", False, ck))
        plans.extend(own)
    return plans, gaps


def replay_patch(backend: Backend, base_files: dict[str, bytes], cand_files: dict[str, bytes], changed: list[tuple[str, str]], *,
                 python_path: list[str] | None = None, generate: bool = True, examples: int = 30, max_callers: int = MAX_CALLERS,
                 confirm: bool = True, progress: Callable[[str], None] | None = None) -> ReplayResult:
    res = ReplayResult()
    t0 = time.perf_counter()
    plans, gaps = plan_targets(changed, cand_files, python_path or [], max_callers)
    res.gaps.extend(gaps)
    tests_c, tests_b = test_paths(cand_files), test_paths(base_files)
    for tp in plans:
        hc = harvest(tp.target, cand_files, tests_c)
        hb = harvest(tp.target, base_files, tests_b) if tp.target.path in base_files else None
        base_keys = {(h.path, h.text) for h in hb.inputs} if hb is not None else set()
        tp.harvest_stats = dict(hc.stats)
        merged = list(hc.inputs)
        keys = {h.key() for h in merged}
        for h in hc.inputs:
            if (h.path, h.text) not in base_keys:
                tp.acknowledged.add(h.key())
        for h in (hb.inputs if hb is not None else []):
            if h.key() not in keys:
                merged.append(h)
                keys.add(h.key())
        merged += [d for d in default_variants(cand_files, tp.target, merged) if d.key() not in keys]
        tp.inputs = merged
    res.timing_ms["harvest"] = round((time.perf_counter() - t0) * 1000, 1)
    if generate:
        t1 = time.perf_counter()
        if not backend.profile.site_packages:
            for tp in plans:
                tp.gen_stats = {"status": "skipped: run profile has site_packages = false (Hypothesis must be installed in the image)"}
        else:
            for root in sorted({tp.target.python_path_root for tp in plans}):
                group = [tp for tp in plans if tp.target.python_path_root == root]
                jobs = []
                planned: set[str] = set()  # a target can serve several changed symbols: generate for it once
                for tp in group:
                    if tp.target.symbol_id in planned:
                        continue
                    planned.add(tp.target.symbol_id)
                    pool = [h for other in group if other.target == tp.target for h in other.inputs]
                    js, st = plan(tp.target, tp.kind, cand_files, pool, n=examples)
                    tp.gen_stats = dict(st)
                    jobs += js
                g = run_generation(backend, cand_files, jobs)
                if "error" in g.stats or "unavailable" in g.stats:
                    res.errors.append(f"generation: {g.stats.get('error') or g.stats.get('unavailable')}")
                by_target: dict[str, list[Harvested]] = {}
                for h in g.inputs:
                    assert h.target is not None
                    by_target.setdefault(h.target.symbol_id, []).append(h)
                for tp in group:
                    have = {h.key() for h in tp.inputs}
                    new = [h for h in by_target.get(tp.target.symbol_id, []) if h.key() not in have]
                    tp.inputs += new
                    tp.gen_stats["generated"] = len(new)
                res.runs.setdefault("generation", []).append({k: v for k, v in g.stats.items() if k != "identity"})
        res.timing_ms["generate"] = round((time.perf_counter() - t1) * 1000, 1)
    # -- runs -------------------------------------------------------------------------------------------
    probes: list[tuple[TargetPlan, Harvested, Probe]] = []
    for tp in plans:
        for h in tp.inputs:
            pid = f"p{len(probes)}"
            probes.append((tp, h, h.probe(pid, tp.target)))
    res.probes = len(probes)
    provenance = _merged(base_files, cand_files)

    def run(files: dict[str, bytes], ps: list[Probe], label: str) -> dict[str, dict[str, Any]]:
        if not ps:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for root in sorted({p.target.python_path_root for p in ps}):
            r: ReplayRun = run_replay(backend, files, [p for p in ps if p.target.python_path_root == root], provenance=provenance)
            out.update(r.observations)
            res.runs.setdefault(label, []).append(r.to_json())
            res.errors.extend(f"{label}: {e}" for e in r.batch_errors)
        if progress:
            progress(f"replay {label}: {len(ps)} probe(s)")
        return out

    t2 = time.perf_counter()
    all_p = [p for _, _, p in probes]
    b1 = run(base_files, all_p, "base")
    b2 = run(base_files, all_p, "base_repeat")
    pc = run(cand_files, all_p, "candidate")
    res.timing_ms["replay"] = round((time.perf_counter() - t2) * 1000, 1)
    classes = {p.id: compare.classify(b1.get(p.id), b2.get(p.id), pc.get(p.id)) for p in all_p}
    differing = [p for p in all_p if classes[p.id] in compare.DIFF_CLASSES]
    again_b: dict[str, dict[str, Any]] = {}
    again_p: list[dict[str, dict[str, Any]]] = []
    t3 = time.perf_counter()
    if differing and confirm:
        again_b = run(base_files, differing, "confirm_base")
        again_p = [run(cand_files, differing, "confirm_candidate"), run(cand_files, differing, "confirm_candidate_repeat")]
    res.timing_ms["confirm"] = round((time.perf_counter() - t3) * 1000, 1)
    examples_out: list[compare.Example] = []
    for tp, h, p in probes:
        c = classes[p.id]
        if c in compare.DIFF_CLASSES and confirm and not compare.confirmed(b1[p.id], pc[p.id], again_b.get(p.id), [a.get(p.id) for a in again_p]):
            c = "unconfirmed"
            res.unconfirmed += 1
        tp.classes[c] = tp.classes.get(c, 0) + 1
        if c in compare.DIFF_CLASSES:
            examples_out.append(compare.Example(tp.symbol, tp.target.symbol_id, tp.relation, c, h.origin, compare.render_call(h, tp.target.qualname),
                                                compare.render_outcome(b1[p.id]), compare.render_outcome(pc[p.id]), h.key() in tp.acknowledged))
    res.clusters = compare.cluster(examples_out)
    res.targets = plans
    executed_by_symbol: dict[str, bool] = {}
    for tp in plans:
        executed_by_symbol[tp.symbol] = executed_by_symbol.get(tp.symbol, False) or tp.executed
    for sym, ok in sorted(executed_by_symbol.items()):
        if not ok:
            req = any(tp.required for tp in plans if tp.symbol == sym)
            n_in = sum(len(tp.inputs) for tp in plans if tp.symbol == sym)
            why = "no input could be harvested or generated" if n_in == 0 else f"none of {n_in} input(s) executed on both sides"
            res.gaps.append({"symbol_id": sym, "required": req, "reason": why})
    res.timing_ms["total"] = round((time.perf_counter() - t0) * 1000, 1)
    return res


def _merged(base: dict[str, bytes], cand: dict[str, bytes]) -> dict[str, Any]:
    """Provenance for inputs harvested from either snapshot: text must occur in one of the versions."""
    out: dict[str, Any] = {}
    for p in set(base) | set(cand):
        vs = tuple(v for v in (cand.get(p), base.get(p)) if v is not None)
        out[p] = vs[0] if len(vs) == 1 or vs[0] == vs[1] else vs
    return out
