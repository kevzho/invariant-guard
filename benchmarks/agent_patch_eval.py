"""Run ``guard verify --execute --replay`` on real coding-agent patches with known outcomes.

Data: SWE-bench Verified instances (``sympy_verified.json``, rows from the Hugging Face
datasets server) and per-run agent patches with their evaluation reports, laid out as
``DATA/<run>/<instance_id>/{patch.diff,report.json}`` (public SWE-bench submissions bucket).
``report.json`` gives the label: resolved means the hidden FAIL_TO_PASS and PASS_TO_PASS
tests passed.

For each (run, instance) the harness checks out the base commit in a scratch clone of the
repository (``DATA/<repo>-src``, its own worktree, never the user's repository), applies only
the agent's patch (not the hidden tests: the guard sees what a reviewer of the agent's patch
would see) and runs the product CLI against the base commit. Config and state live outside
the checkout. Execution happens only in the Docker runner image given by ``--image``.

    .venv/bin/python benchmarks/agent_patch_eval.py --data DATA --image IMAGE \\
        --pick RUN:INSTANCE [--pick ...] [--out results.jsonl]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

CONFIG = """schema_version = "1.0"
[analysis]
quick_budget_ms = 120000
full_budget_ms = 300000
[execution]
enabled = true
profile = "iso"
allow_unisolated = false
[execution.profiles.iso]
backend = "docker"
image = "{image}"
allowed_targets = ["{pkg}/*::*"]
site_packages = true
memory_mb = 1024
cpus = 1.0
wall_seconds = 900
cpu_seconds = 600
max_probes = 64
"""


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=check)


def prepare(src: Path, work: Path, base: str, patch: Path) -> str | None:
    """Base commit checked out in `work` plus the agent patch in the working tree; None, or why it failed."""
    if not work.exists():
        git(src, "worktree", "add", "--detach", str(work), base)
    git(work, "checkout", "-q", "-f", "--detach", base)
    git(work, "clean", "-q", "-fdx")
    p = git(work, "apply", "--whitespace=nowarn", str(patch.resolve()), check=False)
    return None if p.returncode == 0 else f"patch does not apply: {p.stderr.strip()[:300]}"


def summarize(rep: dict) -> dict:
    findings = rep.get("findings", [])
    replay = rep.get("replay") or {}
    return {
        "verdict": rep.get("verdict"),
        "verdict_reasons": rep.get("verdict_reasons", [])[:6],
        "replay_status": replay.get("status"),
        "replay_targets": [{"target": t.get("target"), "executed": t.get("executed"), "classes": t.get("classes"), "note": t.get("reason") or t.get("skipped")}
                           for t in replay.get("targets", [])][:20],
        "findings": [{"result": f.get("property_result"), "severity": f.get("severity"), "symbol": f.get("symbol_id"),
                      "class": (f.get("behavior") or {}).get("difference_class"), "witness": (f.get("behavior") or {}).get("witness"),
                      "acknowledged": (f.get("behavior") or {}).get("acknowledged_by_patch_tests")} for f in findings
                     if f.get("severity") in ("ERROR", "WARNING", "INFO")][:20],
        "timing_ms": rep.get("timing_ms"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--pick", action="append", required=True, help="RUN:INSTANCE_ID")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--guard", default=str(Path(sys.executable).with_name("guard")))
    a = ap.parse_args()
    rows = {r["instance_id"]: r for r in json.loads((a.data / "sympy_verified.json").read_text())}
    out = a.out.open("a") if a.out else None
    for pick in a.pick:
        run, iid = pick.split(":", 1)
        inst = rows[iid]
        repo_name = inst["repo"].split("/")[1]
        pkg = repo_name
        d = a.data / run / iid
        label = json.loads((d / "report.json").read_text())[iid].get("resolved")
        work = a.data / f"{repo_name}-work"
        scratch = a.data / "state" / f"{run}__{iid}"
        scratch.mkdir(parents=True, exist_ok=True)
        cfg = scratch / "config.toml"
        cfg.write_text(CONFIG.format(image=a.image, pkg=pkg))
        rec: dict = {"run": run, "instance": iid, "resolved": bool(label), "base": inst["base_commit"]}
        t0 = time.monotonic()
        err = prepare(a.data / f"{repo_name}-src", work, inst["base_commit"], d / "patch.diff")
        if err:
            rec["error"] = err
        else:
            rec["changed_files"] = git(work, "diff", "--name-only").stdout.split() + git(work, "ls-files", "--others", "--exclude-standard").stdout.split()
            p = subprocess.run([a.guard, "verify", str(work / pkg), "--root", str(work), "--base", inst["base_commit"], "--config", str(cfg),
                                "--state-dir", str(scratch / "guard"), "--execute", "--replay", "--format", "json"],
                               capture_output=True, text=True, timeout=3600)
            rec["exit_code"] = p.returncode
            try:
                rec.update(summarize(json.loads(p.stdout)))
            except json.JSONDecodeError:
                rec["error"] = (p.stderr or p.stdout)[-1500:]
        rec["seconds"] = round(time.monotonic() - t0, 1)
        line = json.dumps(rec)
        print(line, flush=True)
        if out:
            out.write(line + "\n")
            out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
