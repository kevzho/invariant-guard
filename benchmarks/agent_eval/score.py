"""Score agent-proposed contracts on the M4 held-out set with the M4 metrics.

    .venv/bin/python benchmarks/agent_eval/score.py --proposals DIR --out benchmarks/results/agent_eval_<label>.json \\
        [--client NAME/VERSION] [--label TEXT]

DIR holds one JSON candidate batch per symbol (any file name ending in .json; the batch names its
symbol). The batch format is the proposer input form described in README.md and
``agent_candidate_batch.schema.json``. Every batch is submitted through exactly the same code path as
``guard contracts propose`` / MCP ``propose_contracts`` (``contracts.commands.cmd_discover`` with agent
batches), together with the deterministic templates so that "new beyond templates" is measured
against them, in a dry run (nothing is recorded) with dynamic falsification in the capability-checked
Docker runner configured in common.py.

Metrics are computed by ``benchmarks/model_eval.score`` (the M4 evaluation's scorer) with source
``agent_proposed``: schema-valid, nonvacuous, mechanically checkable, distinct validated, refuted,
false claims refuted, seeded bugs exposed, new beyond templates (denominator: raw candidates of the
agent's batches; whole batches rejected before parsing are counted separately).

Honesty rules: the script never creates proposals; a missing symbol is reported as not proposed; if
the runner is unavailable, falsification is RUNNER_UNAVAILABLE and the result says INCOMPLETE (the
checkable/refuted metrics are then not comparable with M4).
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from common import CONFIG, IMAGE, MAX_PROBES, REPO, make_workspace, manifest, slug  # noqa: E402

MAX_FILE_BYTES = 1 << 20


def _sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def load_proposals(directory: Path) -> tuple[list[Any], list[dict[str, Any]]]:
    """Batches from DIR (*.json, sorted by name); unreadable files are reported, not dropped silently."""
    batches: list[Any] = []
    files: list[dict[str, Any]] = []
    for p in sorted(directory.glob("*.json")):
        entry: dict[str, Any] = {"file": p.name}
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                raise ValueError(f"larger than {MAX_FILE_BYTES} bytes")
            doc = json.loads(p.read_text(encoding="utf-8"), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f"constant {c}")))
        except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
            entry.update(status="UNREADABLE", reason=f"{type(exc).__name__}: {str(exc)[:200]}")
            files.append(entry)
            continue
        items = doc["batches"] if isinstance(doc, dict) and set(doc) == {"batches"} else (doc if isinstance(doc, list) else [doc])
        entry.update(status="LOADED", batches=len(items), symbols=[b.get("symbol") if isinstance(b, dict) else None for b in items])
        batches.extend(items)
        files.append(entry)
    return batches, files


def _templates_only(cc: Any, ctx: Any, execute: bool) -> tuple[dict[str, Any], int]:
    out, code = cc.cmd_discover(ctx, cc.DiscoverOptions(dry_run=True, execute=execute, templates=True))
    out["agent"] = {"requested": True, "batches": []}
    return out, code


def score_dir(proposals: Path, *, client: dict[str, str], execute: bool = True, tmp: Path | None = None) -> dict[str, Any]:
    sys.path.insert(0, str(REPO / "src"))
    from invariantguard import ENGINE_VERSION
    from invariantguard.contracts import commands as cc
    from invariantguard.semantic.agent import AGENT_PROTOCOL_VERSION, MAX_BATCHES
    from model_eval import score  # the M4 scorer (benchmarks/model_eval.py)

    m = manifest()
    batches, files = load_proposals(proposals)
    symbols = [f["symbol"] for f in m["functions"]]
    proposed_symbols = sorted({b.get("symbol") for b in batches if isinstance(b, dict) and isinstance(b.get("symbol"), str)})
    result: dict[str, Any] = {
        "schema_version": "1.0", "benchmark": "agent_proposer_eval", "engine_version": ENGINE_VERSION, "protocol_version": AGENT_PROTOCOL_VERSION,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "heldout": {"manifest": "benchmarks/manifests/m4_heldout.json", "functions": len(symbols),
                    "seeded_bugs": sum(1 for f in m["functions"] if f["seeded_bug"])},
        "proposals": {"directory": str(proposals), "files": files, "batches": len(batches), "client": client,
                      "symbols_proposed": proposed_symbols, "symbols_not_proposed": [s for s in symbols if s not in proposed_symbols],
                      "symbols_outside_heldout": [s for s in proposed_symbols if s not in symbols]},
        "settings": {"runner": f"docker {IMAGE}", "max_probes": MAX_PROBES, "execute": execute, "templates_in_same_run": True, "dry_run": True,
                     "config": CONFIG},
        "host": {"python": platform.python_version(), "os": f"{platform.system()} {platform.release()}",
                 "docker": _sh(["docker", "version", "--format", "{{.Server.Version}}"])},
    }
    with tempfile.TemporaryDirectory(prefix="ig-agent-eval-", dir=tmp) as t:
        root = make_workspace(Path(t))
        ctx = cc.open_ctx(root, root / ".guard", root, root / ".guard" / "config.toml")
        t0 = time.perf_counter()
        if len(batches) > MAX_BATCHES:
            raise SystemExit(f"{len(batches)} batches: give one batch per symbol (at most {MAX_BATCHES}, the per-call limit)")
        try:
            # Templates and every agent batch in ONE run, so duplicates of template claims are detected.
            out, code = cc.cmd_discover(ctx, cc.DiscoverOptions(dry_run=True, execute=execute, templates=True, agent_batches=batches,
                                                                agent_client=client)) if batches else _templates_only(cc, ctx, execute)
        finally:
            ctx.close()
        wall = (time.perf_counter() - t0) * 1000
        s = score(out, "agent_proposed", m, root)
    s.update(exit_code=code, wall_ms=round(wall, 1),
             batches=[{k: b.get(k) for k in ("batch_index", "symbol", "status", "rejection_code", "reason", "raw_candidates")} for b in out["agent"]["batches"]],
             template_candidates_same_run=sum(1 for c in out["candidates"] if c["source"] == "template"))
    incomplete = sorted(set(out["incomplete_reasons"]))
    result["run_outcome"] = "ERROR" if out["engine_errors"] else ("INCOMPLETE" if incomplete or not execute else "COMPLETE")
    result["incomplete_reasons"] = incomplete + ([] if execute else ["--no-execute: no falsification; checkable/refuted metrics are not comparable with M4"])
    result["engine_errors"] = out["engine_errors"]
    result["falsification"] = {k: out["falsification"].get(k) for k in ("status", "reason", "results", "elapsed_ms") if k in out["falsification"]}
    result["agent_proposed"] = s
    result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--proposals", type=Path, required=True, help="directory with one candidate batch JSON per symbol")
    ap.add_argument("--out", type=Path, required=True, help="results JSON to write")
    ap.add_argument("--client", default="unidentified-agent", metavar="NAME[/VERSION]", help="the proposing agent (recorded, self-reported)")
    ap.add_argument("--label", default=None, help="free-text label recorded in the results")
    ap.add_argument("--no-execute", action="store_true", help="skip falsification (validation-only metrics; marked INCOMPLETE)")
    args = ap.parse_args()
    if not args.proposals.is_dir():
        ap.error(f"{args.proposals} is not a directory")
    name, _, version = args.client.partition("/")
    res = score_dir(args.proposals, client={"name": name, "version": version}, execute=not args.no_execute)
    res["label"] = args.label
    res["command"] = " ".join([".venv/bin/python", "benchmarks/agent_eval/score.py", *sys.argv[1:]])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=2, default=str) + "\n", encoding="utf-8")
    s = res["agent_proposed"]
    print(f"{res['run_outcome']}: {s['raw_candidates']} candidates, {s['nonvacuous']} nonvacuous, {s['refuted']} refuted, "
          f"seeded bugs exposed {s['seeded_bug_functions_exposed']}; wrote {args.out}", file=sys.stderr)
    return 0 if res["run_outcome"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["score_dir", "load_proposals", "slug"]
