"""Run replay probes on one snapshot through a runner backend (protocol ig-replay-1).

This module only executes and validates; comparing base and patch observations, the
evidence mapping and coverage are later phases. Guarantees enforced here:

* every target must be allowlisted by the run profile (``allowed_targets``), else the
  probe is ``not_allowed`` and never sent;
* inputs are typed: an encoded built-in ``Value``, a ``Construct`` receiver, or an
  ``Expr`` whose text occurs verbatim in the named repository file (``provenance``) -
  so no model- or agent-supplied text is ever executed;
* responses are untrusted: every observation is shape-checked and bounded, unknown or
  malformed ones become ``invalid_observation``; a batch whose output exceeds the
  profile's ``max_output_bytes`` is split and retried, so one large value loses at most
  its own probe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

from ..execution.runner import Backend, HARNESSES, Workspace, harness_digest
from ..frontends.python.resolve import module_name_for
from ..models.spans import digest_json
from .values import check_node, encode_input

STATUSES = ("ok", "import_error", "resolve_error", "input_error", "timeout", "crashed", "harness_error")
MAX_PROBES_PER_BATCH = 256


@dataclass(frozen=True)
class Value:
    node: dict[str, Any]

    @classmethod
    def of(cls, value: Any) -> "Value":
        return cls(encode_input(value))

    def spec(self, ns_ids: dict[Any, str]) -> dict[str, Any]:
        return {"v": self.node}


@dataclass(frozen=True)
class Namespace:
    """Names visible to a repository expression: a module's globals and/or explicit imports."""

    module_globals: str | None = None
    imports: tuple[tuple[str, str, str | None], ...] = ()  # (local name, module, attribute or None)

    def to_json(self) -> dict[str, Any]:
        return {"module_globals": self.module_globals, "imports": [list(i) for i in self.imports]}


@dataclass(frozen=True)
class Expr:
    """A repository-authored expression (from a test or doctest), with its source location."""

    text: str
    path: str
    line: int
    namespace: Namespace = Namespace()

    def spec(self, ns_ids: dict[Any, str]) -> dict[str, Any]:
        return {"x": self.text, "ns": ns_ids[self.namespace]}


Input = Union[Value, Expr]


@dataclass(frozen=True)
class Construct:
    """Receiver built by calling the target's owning class."""

    args: tuple[Input, ...] = ()
    kwargs: tuple[tuple[str, Input], ...] = ()


@dataclass(frozen=True)
class Target:
    path: str  # snapshot-relative module path
    qualname: str
    python_path_root: str = "."

    @property
    def symbol_id(self) -> str:
        return f"{self.path}::{self.qualname}"

    @property
    def module(self) -> str:
        rel = self.path
        root = self.python_path_root.strip("/")
        if root not in ("", "."):
            if not rel.startswith(root + "/"):
                raise ValueError(f"{rel} is not under python_path_root {root}")
            rel = rel[len(root) + 1:]
        mod = module_name_for(rel)
        if mod is None:
            raise ValueError(f"{self.path} is not an importable module path")
        return mod


@dataclass
class Probe:
    id: str
    target: Target
    args: tuple[Input, ...] = ()
    kwargs: tuple[tuple[str, Input], ...] = ()
    receiver: Input | Construct | None = None
    seed: int = 0

    def inputs(self) -> list[Input]:
        out = list(self.args) + [v for _, v in self.kwargs]
        if isinstance(self.receiver, Construct):
            out += list(self.receiver.args) + [v for _, v in self.receiver.kwargs]
        elif self.receiver is not None:
            out.append(self.receiver)
        return out


@dataclass
class ReplayRun:
    observations: dict[str, dict[str, Any]]
    identity: dict[str, Any] | None
    harness_digest: str
    environment_digest: str | None
    batches: int = 0
    batch_errors: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"harness": HARNESSES["replay"][1], "harness_digest": self.harness_digest, "environment_digest": self.environment_digest,
                "identity": self.identity, "batches": self.batches, "batch_errors": self.batch_errors}


class InputRejected(ValueError):
    """An input that may not be sent (wrong type, or expression text not found in the repository)."""


def check_inputs(probe: Probe, provenance: dict[str, bytes]) -> None:
    for inp in probe.inputs():
        if isinstance(inp, Value):
            continue
        if not isinstance(inp, Expr):
            raise InputRejected(f"probe {probe.id}: input of type {type(inp).__name__} is not a Value or Expr")
        src = provenance.get(inp.path)
        if src is None:
            raise InputRejected(f"probe {probe.id}: expression source {inp.path} is not in the snapshot")
        lines = src.decode("utf-8", "replace").splitlines()
        window = "\n".join(lines[max(0, inp.line - 1): inp.line + 40])
        if inp.text not in window and " ".join(inp.text.split()) not in " ".join(window.split()):
            raise InputRejected(f"probe {probe.id}: expression text does not occur at {inp.path}:{inp.line}")


def _probe_spec(p: Probe, tid: str, ns_ids: dict[Namespace, str]) -> dict[str, Any]:
    out: dict[str, Any] = {"id": p.id, "tid": tid, "seed": p.seed, "args": [a.spec(ns_ids) for a in p.args],
                           "kwargs": {k: v.spec(ns_ids) for k, v in p.kwargs}}
    if isinstance(p.receiver, Construct):
        out["receiver"] = {"construct": {"args": [a.spec(ns_ids) for a in p.receiver.args], "kwargs": {k: v.spec(ns_ids) for k, v in p.receiver.kwargs}}}
    elif p.receiver is not None:
        out["receiver"] = p.receiver.spec(ns_ids)
    return out


def validate_observation(o: Any) -> dict[str, Any]:
    """Shape-check one untrusted observation; malformed ones become ``invalid_observation``."""
    def bad(why: str) -> dict[str, Any]:
        return {"status": "invalid_observation", "detail": why}

    if type(o) is not dict or o.get("status") not in STATUSES:
        return bad(f"unknown status {str(o.get('status') if type(o) is dict else o)[:60]!r}")
    if o["status"] != "ok":
        return {k: o[k] for k in ("status", "detail", "elapsed_ms") if k in o}
    res = o.get("result")
    if type(res) is not dict or res.get("kind") not in ("returned", "raised"):
        return bad("result missing")
    if res["kind"] == "returned":
        why = check_node(res.get("value"))
        if why:
            return bad(why)
    else:
        e = res.get("exception")
        if type(e) is not dict or type(e.get("type")) is not str or type(e.get("message")) is not str:
            return bad("exception summary malformed")
    for key in ("mutated",):
        if key in o:
            if type(o[key]) is not dict or any(check_node(v) for v in o[key].values()):
                return bad(f"{key} malformed")
    if "receiver_after" in o and check_node(o["receiver_after"]):
        return bad("receiver_after malformed")
    return {k: o[k] for k in ("status", "result", "mutated", "receiver_after", "elapsed_ms") if k in o}


def run_replay(backend: Backend, files: dict[str, bytes], probes: list[Probe], *, provenance: dict[str, bytes],
               iter_prefix: int = 100, max_observation_bytes: int = 64 * 1024, chunk: int = 10) -> ReplayRun:
    """Run ``probes`` against the snapshot ``files``; returns one observation per probe id.

    With the profile's ``site_packages`` the image's installed distributions are importable
    by target code, and the environment digest covers their names and versions.
    """
    st = backend.status()
    site_packages = backend.profile.site_packages
    run = ReplayRun({}, None, harness_digest("replay"), st.environment_digest)
    if len({p.id for p in probes}) != len(probes):
        raise ValueError("probe ids must be unique")
    runnable: list[Probe] = []
    for p in probes:
        if not backend.profile.allows(p.target.symbol_id):
            run.observations[p.id] = {"status": "not_allowed", "detail": f"run profile {backend.profile.name!r} does not allowlist {p.target.symbol_id}"}
            continue
        check_inputs(p, provenance)
        runnable.append(p)
    if not st.available:
        for p in runnable:
            run.observations[p.id] = {"status": "runner_unavailable", "detail": st.reason}
        return run
    ws = Workspace(files, "replay")
    try:
        pending = [runnable[i:i + MAX_PROBES_PER_BATCH] for i in range(0, len(runnable), MAX_PROBES_PER_BATCH)]
        while pending:
            batch = pending.pop(0)
            if not batch:
                continue
            resp, err = _run_batch(backend, ws, batch, site_packages, iter_prefix, max_observation_bytes, chunk)
            run.batches += 1
            if resp is None and err and "max_output_bytes" in err and len(batch) > 1:
                half = len(batch) // 2
                pending[:0] = [batch[:half], batch[half:]]
                continue
            if resp is None:
                run.batch_errors.append(err or "unknown runner error")
                for p in batch:
                    run.observations[p.id] = {"status": "batch_error", "detail": err}
                continue
            run.identity = run.identity or resp.get("identity")
            results = resp.get("results") if type(resp.get("results")) is dict else {}
            if resp.get("batch_error"):
                run.batch_errors.append(str(resp["batch_error"])[:300])
            for p in batch:
                o = results.get(p.id)
                run.observations[p.id] = validate_observation(o) if o is not None else {"status": "batch_error", "detail": resp.get("batch_error") or "no observation returned"}
    finally:
        ws.close()
    if site_packages and run.identity is not None:
        dists = run.identity.get("distributions")
        if not isinstance(dists, list) or any(type(d) is not str or d.startswith("<error") for d in dists):
            run.batch_errors.append("installed distributions could not be listed; the dependency environment is unidentified")
            run.environment_digest = None
        else:
            run.environment_digest = digest_json({"runner": st.environment_digest, "site_packages": sorted(dists)})
    return run


def _run_batch(backend: Backend, ws: Workspace, batch: list[Probe], site_packages: bool, iter_prefix: int, max_obs: int, chunk: int) -> tuple[dict[str, Any] | None, str | None]:
    targets: dict[str, dict[str, Any]] = {}
    tids: dict[Target, str] = {}
    ns_ids: dict[Namespace, str] = {}
    roots = {p.target.python_path_root for p in batch}
    if len(roots) != 1:
        raise ValueError("one batch must share a python_path_root")
    for p in batch:
        if p.target not in tids:
            tids[p.target] = f"t{len(tids)}"
            targets[tids[p.target]] = {"module": p.target.module, "qualname": p.target.qualname}
        for inp in p.inputs():
            if isinstance(inp, Expr) and inp.namespace not in ns_ids:
                ns_ids[inp.namespace] = f"n{len(ns_ids)}"
    prof = backend.profile
    req = {"mode": "replay", "src_root": backend.src_root(ws), "python_path_root": roots.pop(), "site_packages": site_packages,
           "targets": targets, "namespaces": {v: k.to_json() for k, v in ns_ids.items()},
           "probes": [_probe_spec(p, tids[p.target], ns_ids) for p in batch],
           "probe_wall_seconds": prof.probe_wall_seconds, "probe_cpu_seconds": max(1, int(min(prof.cpu_seconds, prof.probe_wall_seconds * 2))),
           "batch_wall_seconds": max(1.0, prof.wall_seconds - 5), "iter_prefix": iter_prefix, "max_observation_bytes": max_obs, "chunk": chunk}
    r = backend.run(ws, req, harness="replay")
    return r.response, r.error
