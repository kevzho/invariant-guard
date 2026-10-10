"""Plan and run in-boundary Hypothesis generation of replay inputs (host side).

Three sources, recorded as the input's origin:

* ``shape``: values shaped like a harvested literal argument (same type and structure,
  nearby magnitudes, the observed string alphabet). Expression arguments, the receiver
  and the call sequence of that harvested input are kept unchanged.
* ``annotation``: ``from_type`` on the target's inline annotations, when every required
  parameter is annotated.
* ``stub``: ``from_type`` on the matching ``.pyi`` stub's annotations, plus the stub's
  imports and TypeVars. The annotation text must occur verbatim in the stub.

Hypothesis runs only inside the boundary (it imports nothing from the target except for
``annotation``, which resolves the target to read its hints) and only when the image
provides it (profile ``site_packages``); otherwise generation is reported as unavailable,
never silently skipped. Draws are derandomized with no example database, so the same
plan in the same environment yields the same inputs. Only examples that round-trip
through the built-in encoding are kept.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from ..execution.runner import Backend, Workspace
from .harvest import Harvested, import_map, literal
from .run import Input, Target, Value
from .values import check_node, decode_input

MAX_SHAPE_JOBS = 8
DEFAULT_EXAMPLES = 30


@dataclass
class GenJob:
    id: str
    target: Target
    origin: str  # shape | annotation | stub
    vary: dict[str, dict[str, Any]]  # slot -> strategy spec; slots are "a<i>" (positional) or "k:<name>"
    base: Harvested | None = None  # shape: the harvested input whose Value slots are varied
    receiver: Input | None = None  # annotation/stub on methods: a harvested receiver
    n: int = DEFAULT_EXAMPLES


@dataclass
class Generated:
    inputs: list[Harvested] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def bump(self, k: str, n: int = 1) -> None:
        self.stats[k] = self.stats.get(k, 0) + n


def _def_node(tree: ast.Module, qualname: str) -> ast.AST | None:
    scope: list[ast.stmt] = tree.body
    node = None
    for p in qualname.split("."):
        node = next((s for s in scope if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and s.name == p), None)
        if node is None:
            return None
        scope = node.body
    if isinstance(node, ast.ClassDef):
        node = next((s for s in node.body if isinstance(s, ast.FunctionDef) and s.name == "__init__"), None)
    return node


def _signature(fn: ast.FunctionDef | ast.AsyncFunctionDef, method: bool) -> list[tuple[str, bool, ast.AST | None]] | None:
    """(name, required, annotation node) per parameter without self/cls; None when a positional-only
    parameter exists (typed inputs are passed by keyword)."""
    a = fn.args
    allpos = a.posonlyargs + a.args
    n_def = len(a.defaults)
    out = []
    for i, p in enumerate(allpos):
        if method and i == 0:
            continue
        if p in a.posonlyargs:
            return None
        out.append((p.arg, i < len(allpos) - n_def, p.annotation))
    out += [(p.arg, d is None, p.annotation) for p, d in zip(a.kwonlyargs, a.kw_defaults)]
    return out


def _stub_path(target: Target, files: dict[str, bytes]) -> str | None:
    cand = target.path[:-3] + ".pyi"
    return cand if cand in files else None


def _annotation_vary(params, text_of, extra: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    if params is None:
        return None
    vary = {}
    for name, required, ann in params:
        if ann is None:
            if required:
                return None
            continue
        text = text_of(ann)
        if text is None:
            return None
        vary["k:" + name] = {**extra, "ann": text, "param": name}
    return vary or None


def plan(target: Target, kind: str, files: dict[str, bytes], harvested: list[Harvested], n: int = DEFAULT_EXAMPLES) -> tuple[list[GenJob], dict[str, int]]:
    """Generation jobs for one target (no execution)."""
    stats: dict[str, int] = {}
    jobs: list[GenJob] = []
    receiver = next((h.receiver for h in harvested if h.receiver is not None and not h.steps), None)
    # shape: vary the literal arguments of distinct harvested calls
    seen_shapes: set[str] = set()
    for h in harvested:
        vary: dict[str, dict[str, Any]] = {}
        for i, a in enumerate(h.args):
            if isinstance(a, Value):
                vary[f"a{i}"] = {"k": "shape", "value": a.node}
        for k, a in h.kwargs:
            if isinstance(a, Value):
                vary[f"k:{k}"] = {"k": "shape", "value": a.node}
        if not vary:
            continue
        sig = repr(sorted((k, v["value"].get("t")) for k, v in vary.items())) + repr([type(a).__name__ for a in h.args])
        if sig in seen_shapes:
            continue
        seen_shapes.add(sig)
        jobs.append(GenJob(f"{target.symbol_id}#shape{len(jobs)}", target, "shape", vary, base=h, n=n))
        if sum(1 for j in jobs if j.origin == "shape") >= MAX_SHAPE_JOBS:
            break
    try:
        tree = ast.parse(files[target.path])
    except (KeyError, SyntaxError):
        return jobs, stats
    fn = _def_node(tree, target.qualname)
    if kind == "property" or not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return jobs, stats
    needs_receiver = kind in ("method",)
    if needs_receiver and receiver is None:
        stats["typed_no_receiver"] = 1
    else:
        params = _signature(fn, method=kind in ("method", "classmethod", "class"))
        text = files[target.path].decode("utf-8", "replace")
        vary = _annotation_vary(params, lambda a: ast.get_source_segment(text, a), {"k": "annotation"})
        if vary:
            jobs.append(GenJob(f"{target.symbol_id}#annotation", target, "annotation", vary, receiver=receiver if needs_receiver else None, n=n))
        else:
            stats["annotation_incomplete"] = 1
        sp = _stub_path(target, files)
        if sp is not None:
            stext = files[sp].decode("utf-8", "replace")
            try:
                stree = ast.parse(stext)
            except SyntaxError:
                stree = None
            sfn = _def_node(stree, target.qualname) if stree is not None else None
            if isinstance(sfn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                imps, _ = import_map(stree.body)
                typevars = [st.targets[0].id for st in stree.body if isinstance(st, ast.Assign) and len(st.targets) == 1
                            and isinstance(st.targets[0], ast.Name) and isinstance(st.value, ast.Call) and ast.unparse(st.value.func).endswith("TypeVar")]
                sv = _annotation_vary(_signature(sfn, method=kind in ("method", "classmethod", "class")), lambda a: ast.get_source_segment(stext, a),
                                      {"k": "stub", "imports": sorted([k, *v] for k, v in imps.items()), "typevars": typevars, "path": sp})
                if sv:
                    jobs.append(GenJob(f"{target.symbol_id}#stub", target, "stub", sv, receiver=receiver if needs_receiver else None, n=n))
                else:
                    stats["stub_incomplete"] = 1
    return jobs, stats


def _check_stub_text(job: GenJob, files: dict[str, bytes]) -> bool:
    for spec in job.vary.values():
        if spec["k"] == "stub" and spec["ann"] not in files.get(spec["path"], b"").decode("utf-8", "replace"):
            return False
        if spec["k"] == "annotation" and spec["ann"] not in files.get(job.target.path, b"").decode("utf-8", "replace"):
            return False
    return True


def run_generation(backend: Backend, files: dict[str, bytes], jobs: list[GenJob], *, job_wall_seconds: float = 20.0) -> Generated:
    """Run generation jobs in the boundary (one batch) and build inputs from the examples."""
    out = Generated()
    if not jobs:
        return out
    st = backend.status()
    if not st.available:
        out.stats["unavailable"] = st.reason
        return out
    if len({j.id for j in jobs}) != len(jobs):
        raise ValueError("generation job ids must be unique")
    runnable = []
    for j in jobs:
        if not backend.profile.allows(j.target.symbol_id):
            out.bump("not_allowed")
        elif not _check_stub_text(j, files):
            out.bump("annotation_text_not_in_repository")
        else:
            runnable.append(j)
    if not runnable:
        return out
    roots = {j.target.python_path_root for j in runnable}
    if len(roots) != 1:
        raise ValueError("one generation batch must share a python_path_root")
    tids: dict[Target, str] = {}
    targets = {}
    for j in runnable:
        if j.target not in tids:
            tids[j.target] = f"t{len(tids)}"
            targets[tids[j.target]] = {"module": j.target.module, "qualname": j.target.qualname}
    prof = backend.profile
    req = {"mode": "generate", "src_root": "", "python_path_root": roots.pop(), "site_packages": prof.site_packages, "targets": targets,
           "jobs": [{"id": j.id, "tid": tids[j.target], "n": j.n,
                     "vary": {slot: ({"k": "shape", "value": s["value"]} if s["k"] == "shape" else {k: v for k, v in s.items() if k != "path"})
                              for slot, s in j.vary.items()}} for j in runnable],
           "job_wall_seconds": job_wall_seconds, "probe_cpu_seconds": max(1, int(min(prof.cpu_seconds, job_wall_seconds)))}
    ws = Workspace(files, "generate")
    try:
        req["src_root"] = backend.src_root(ws)
        r = backend.run(ws, req, harness="replay")
    finally:
        ws.close()
    if r.response is None:
        out.stats["error"] = r.error
        return out
    out.stats["identity"] = r.response.get("identity")
    results = r.response.get("results") if type(r.response.get("results")) is dict else {}
    for j in runnable:
        res = results.get(j.id)
        if type(res) is not dict or res.get("status") != "ok" or type(res.get("examples")) is not list:
            out.bump(f"{j.origin}_{res.get('status') if type(res) is dict else 'missing'}")
            continue
        if res.get("unsupported"):
            out.bump(f"{j.origin}_unsupported")
        for ex in res["examples"]:
            h = _build(j, ex)
            if h is None:
                out.bump("invalid_example")
            else:
                out.inputs.append(h)
        out.bump(f"{j.origin}_examples", len(res["examples"]))
    return out


def _build(job: GenJob, ex: Any) -> Harvested | None:
    if type(ex) is not dict or set(ex) != set(job.vary) or any(check_node(v) for v in ex.values()):
        return None
    try:
        for node in ex.values():
            decode_input(node)  # only round-trippable built-in values become inputs
    except Exception:  # noqa: BLE001 - untrusted example
        return None
    vals = {slot: Value(node) for slot, node in ex.items()}
    if job.origin == "shape":
        b = job.base
        assert b is not None
        args = tuple(vals.get(f"a{i}", a) for i, a in enumerate(b.args))
        kwargs = tuple((k, vals.get(f"k:{k}", a)) for k, a in b.kwargs)
        return Harvested("shape", args, kwargs, b.receiver, b.steps, b.path, b.line, f"{b.text} [shape]", job.target)
    args: tuple[Input, ...] = ()
    kwargs = tuple((slot[2:], vals[slot]) for slot in job.vary)
    where = next(iter(job.vary.values())).get("path", job.target.path)
    return Harvested(job.origin, args, kwargs, job.receiver, (), where, 0, f"{job.target.qualname} [{job.origin}]", job.target)


__all__ = ["GenJob", "Generated", "plan", "run_generation", "literal"]
