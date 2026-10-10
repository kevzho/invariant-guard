"""Phase 4 evaluation of the shipped differential replay on the pivot corpus.

Runs the product code path of ``guard verify --replay`` (static change detection and
slice, ``replay.engine.replay_patch``, ``replay.evidence.build``) on the 30 most recent
package commits of more-itertools, boltons and dateutil (``spikes/corpus``, pinned in
``spikes/corpus.lock``), plus the spike's 20 seeded mutants. Nothing is checked out:
both sides come from Git objects. Execution happens only in the Docker runner with the
replay image (python:3.13-slim + pinned hypothesis and six; ``spikes/diffreplay/Dockerfile``).

    .venv/bin/python benchmarks/replay_eval.py [--repos more-itertools,boltons,dateutil] [--mutants] [--out FILE]

Writes one JSON record per commit / mutant; ``--summary FILE`` prints the metrics.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPIKE = REPO_ROOT / "spikes" / "diffreplay"
sys.path.insert(0, str(SPIKE))

from changes import REPOS, pkg_info, recent_commits, repo  # noqa: E402

from invariantguard import ENGINE_VERSION  # noqa: E402
from invariantguard.config import Config  # noqa: E402
from invariantguard.execution.profiles import parse_profile  # noqa: E402
from invariantguard.execution.runner import DockerBackend  # noqa: E402
from invariantguard.impact.graph import affected_slice, diff_files, match_symbols, symbol_table  # noqa: E402
from invariantguard.impact.keys import snapshot_keys  # noqa: E402
from invariantguard.orchestration import SEMANTICS_PROFILE, lower_snapshot  # noqa: E402
from invariantguard.replay import engine, evidence  # noqa: E402
from invariantguard.snapshots.capture import capture_git_revision  # noqa: E402
from invariantguard.verify import REPLAY_EXCLUDE  # noqa: E402

IMAGE = "invariantguard-diffreplay:spike"
PROFILE = {"backend": "docker", "image": IMAGE, "allowed_targets": ["*::*"], "site_packages": True, "memory_mb": 512, "max_processes": 64,
           "cpus": 1.0, "wall_seconds": 900, "cpu_seconds": 600, "probe_wall_seconds": 2.0, "max_output_bytes": 64 << 20}


def changed_symbols(r, parent: str, commit: str, pkg: str, exclude: list[str]) -> list[tuple[str, str]]:
    """What `guard verify` would replay: modified symbols plus direct readers of changed module data (static, .py only)."""
    cfg = Config()
    bs = capture_git_revision(r, parent, r.toplevel, [pkg], [".git", *exclude], SEMANTICS_PROFILE)
    cs = capture_git_revision(r, commit, r.toplevel, [pkg], [".git", *exclude], SEMANTICS_PROFILE)
    lb, lc = lower_snapshot(bs, cfg), lower_snapshot(cs, cfg)
    _, kb = snapshot_keys(lb, cfg, "eval", SEMANTICS_PROFILE)
    _, kc = snapshot_keys(lc, cfg, "eval", SEMANTICS_PROFILE)
    sb, sc = symbol_table(lb, kb), symbol_table(lc, kc)
    files = diff_files(bs, cs)
    m = match_symbols(sb, sc, files)
    sl = affected_slice(lb, lc, sb, sc, m, files, None)
    return engine.changed_for_replay(list(m.modified), sl.reasons, set(sc))


def _def_source(src: str, qualname: str) -> str:
    import ast

    scope = ast.parse(src).body
    node = None
    for p in qualname.split("."):
        node = next(s for s in scope if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and s.name == p)
        scope = node.body
    return ast.get_source_segment(src, node)


def run_case(backend, bfiles, cfiles, changed, pyroot, cand_id, base_id) -> dict:
    t0 = time.perf_counter()
    res = engine.replay_patch(backend, bfiles, cfiles, changed, python_path=[] if pyroot == "." else [pyroot])
    out = evidence.build(res, cand_files=cfiles, cand_snapshot_id=cand_id, base_snapshot_id=base_id, run_id="run-" + "0" * 32,
                         created_at="2026-10-09T00:00:00Z", backend_status=backend.status(), run_profile_digest=None, engine_version=ENGINE_VERSION)
    return {"findings": [{"symbol": f["symbol_id"], "severity": f["severity"], "class": f["behavior"]["difference_class"],
                          "target": f["behavior"]["observed_through"]["target"], "relation": f["behavior"]["observed_through"]["relation"],
                          "call": f["behavior"]["witness"]["call"], "base": f["behavior"]["witness"]["base"], "patch": f["behavior"]["witness"]["patch"],
                          "origin": f["behavior"]["witness"]["input_origin"], "origins": f["behavior"]["input_origins"],
                          "n": f["behavior"]["examples_in_cluster"], "ack": f["behavior"]["acknowledged_by_patch_tests"]} for f in out["findings"]],
            "status": out["section"]["status"], "targets": out["section"]["targets"], "coverage": out["coverage"], "probes": res.probes,
            "unconfirmed": res.unconfirmed, "errors": res.errors[:20], "timing_ms": res.timing_ms, "wall_s": round(time.perf_counter() - t0, 1),
            "incomplete_reasons": out["incomplete_reasons"]}


def snapshots(r, parent: str, commit: str):
    b = capture_git_revision(r, parent, r.toplevel, ["."], REPLAY_EXCLUDE + ["docs"], SEMANTICS_PROFILE, suffixes=(".py", ".pyi"))
    c = capture_git_revision(r, commit, r.toplevel, ["."], REPLAY_EXCLUDE + ["docs"], SEMANTICS_PROFILE, suffixes=(".py", ".pyi"))
    return b, c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", default="more-itertools,boltons,dateutil")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--mutants", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated commit prefixes to (re)run")
    ap.add_argument("--resume", action="store_true", help="skip commits / mutants already recorded in --out")
    ap.add_argument("--out", default=str(REPO_ROOT / "benchmarks" / "results" / "replay_phase4.jsonl"))
    a = ap.parse_args()
    backend = DockerBackend(parse_profile("eval", dict(PROFILE)))
    st = backend.status()
    if not st.available:
        sys.exit(f"runner unavailable: {st.reason}")
    done: set[str] = set()
    if a.resume and Path(a.out).exists():
        for line in open(a.out, encoding="utf-8"):
            rec = json.loads(line)
            if rec.get("status") != "EVAL_ERROR":
                done.add(rec.get("id") or rec["commit"])
    out = open(a.out, "a", encoding="utf-8")
    if a.mutants:
        for mu in json.load(open(SPIKE / "mutants.json")):
            if mu["id"] in done:
                continue
            r = repo(mu["repo"])
            full = r.resolve_commit(mu["commit"])
            bsnap, _ = snapshots(r, full, full)
            bfiles = {f.path: f.raw for f in bsnap.files}
            src = bfiles[mu["path"]].decode()
            seg = _def_source(src, mu["qualname"])  # mutate inside the target's own definition (as spikes/diffreplay/seeded.py)
            assert seg.count(mu["old"]) == 1, mu["id"]
            cfiles = dict(bfiles, **{mu["path"]: src.replace(seg, seg.replace(mu["old"], mu["new"]), 1).encode()})
            _, pyroot = pkg_info(mu["repo"], bfiles)
            rec = run_case(backend, bfiles, cfiles, [(f"{mu['path']}::{mu['qualname']}", "modified")], pyroot, bsnap.snapshot_id, bsnap.snapshot_id)
            rec.update(kind="mutant", id=mu["id"], repo=mu["repo"], commit=mu["commit"], symbol=f"{mu['path']}::{mu['qualname']}", category=mu["category"])
            out.write(json.dumps(rec) + "\n")
            out.flush()
            print(mu["id"], len(rec["findings"]), rec["wall_s"], flush=True)
        return
    for name in a.repos.split(","):
        r = repo(name)
        for c in recent_commits(name, a.n):
            if a.only and not any(c["commit"].startswith(x) for x in a.only.split(",")):
                continue
            if c["commit"] in done:
                continue
            t0 = time.perf_counter()
            try:
                bsnap, csnap = snapshots(r, c["parent"], c["commit"])
                bfiles, cfiles = {f.path: f.raw for f in bsnap.files}, {f.path: f.raw for f in csnap.files}
                pkg, pyroot = pkg_info(name, cfiles)
                changed = changed_symbols(r, c["parent"], c["commit"], pkg, REPOS[name]["exclude"])
                rec = run_case(backend, bfiles, cfiles, changed, pyroot, csnap.snapshot_id, bsnap.snapshot_id) if changed else {"status": "NOTHING_TO_REPLAY", "findings": []}
                rec["changed"] = changed
            except Exception as exc:  # noqa: BLE001 - recorded, never hidden
                rec = {"status": "EVAL_ERROR", "error": f"{type(exc).__name__}: {exc}"[:500], "findings": []}
            rec.update(kind="commit", repo=name, commit=c["commit"], subject=c["subject"], total_s=round(time.perf_counter() - t0, 1))
            out.write(json.dumps(rec) + "\n")
            out.flush()
            print(name, c["commit"][:8], rec["status"], len(rec["findings"]), rec["total_s"], flush=True)


if __name__ == "__main__":
    main()
