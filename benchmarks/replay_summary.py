"""Metrics for benchmarks/replay_eval.py output, against the spike's labels and commit judgements.

    .venv/bin/python benchmarks/replay_summary.py > benchmarks/results/replay_phase4_summary.json

Inputs: ``replay_phase4.jsonl`` (corpus), ``replay_phase4_mutants.jsonl`` (seeded mutants),
``spikes/diffreplay/labels.json`` (hand labels of the spike's 38 difference clusters) and
``spikes/diffreplay/results/commit_recall.json`` (per-commit "intends an observable
change" judgements). Findings that match no spike label are listed for manual labeling.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "benchmarks" / "results"
SPIKE = ROOT / "spikes" / "diffreplay"
PROBABLE_REGRESSION = ("more-itertools", "caaec943")


def main() -> None:
    latest: dict[str, dict] = {}
    for line in open(RES / "replay_phase4.jsonl"):
        rec = json.loads(line)
        latest[rec["commit"]] = rec  # a resumed run may re-record a commit: the last record wins
    rows = list(latest.values())
    labels = json.load(open(SPIKE / "labels.json"))
    judg = {(r["repo"], r["commit"][:8]): r for r in json.load(open(SPIKE / "results" / "commit_recall.json"))["rows"]}
    lab = {}
    for x in labels:
        lab.setdefault((x["repo"], x["commit"][:8], x["symbol"].split("::")[-1]), []).append(x)
    out: dict = {"commits": len(rows), "status": dict(Counter(r["status"] for r in rows))}
    # coverage over changed symbols
    changed = executed = req = req_exec = 0
    for r in rows:
        for sym, _ in r.get("changed", []):
            ts = [t for t in r.get("targets", []) if t["symbol_id"] == sym]
            ok = any(t["executed"] for t in ts)
            changed += 1
            executed += ok
            if any(t["required"] for t in ts) or not ts:
                req += 1
                req_exec += ok
    out["changed_symbols"] = changed
    out["changed_symbols_executed"] = executed
    out["changed_public_symbols"] = req
    out["changed_public_symbols_executed"] = req_exec
    # noise and recall
    replayed = [r for r in rows if any(t["executed"] for t in r.get("targets", []))]
    correct = [r for r in replayed if (r["repo"], r["commit"][:8]) != PROBABLE_REGRESSION]
    warn_commits = [r for r in correct if any(f["severity"] == "WARNING" for f in r["findings"])]
    any_commits = [r for r in correct if r["findings"]]
    out["commits_with_executed_replay"] = len(replayed)
    out["noise_correct_commits_with_warning"] = f"{len(warn_commits)}/{len(correct)} = {100 * len(warn_commits) / max(1, len(correct)):.1f}%"
    out["correct_commits_with_any_finding"] = f"{len(any_commits)}/{len(correct)}"
    pr = next((r for r in rows if (r["repo"], r["commit"][:8]) == PROBABLE_REGRESSION), None)
    out["probable_regression_caaec94"] = [{k: f[k] for k in ("symbol", "severity", "class", "call", "base", "patch")} for f in pr["findings"]] if pr else "not run"
    intends = [r for r in replayed if judg.get((r["repo"], r["commit"][:8]), {}).get("intends_change")]
    no_intent = [r for r in replayed if (r["repo"], r["commit"][:8]) in judg and not judg[(r["repo"], r["commit"][:8])]["intends_change"]]
    out["recall_commits_intending_change"] = f"{sum(1 for r in intends if r['findings'])}/{len(intends)}"
    out["no_change_commits_with_findings"] = [f"{r['repo']} {r['commit'][:8]}" for r in no_intent if r["findings"]]
    # findings vs spike labels
    fl = Counter()
    unlabeled = []
    for r in rows:
        for f in r["findings"]:
            key = (r["repo"], r["commit"][:8], f["symbol"].split("::")[-1])
            cands = lab.get(key, [])
            match = next((x for x in cands if x["class"] == f["class"]), cands[0] if cands else None)
            label = match["label"] if match else "UNLABELED"
            fl[(f["severity"], label)] += 1
            if not match:
                unlabeled.append({"repo": r["repo"], "commit": r["commit"][:8], "subject": r["subject"], **f})
    out["findings"] = sum(len(r["findings"]) for r in rows)
    out["findings_by_severity_and_spike_label"] = {f"{s}/{l}": n for (s, l), n in sorted(fl.items())}
    out["findings_by_class"] = dict(Counter(f["class"] for r in rows for f in r["findings"]))
    out["unlabeled_findings"] = unlabeled
    walls = [r["total_s"] for r in rows if r["status"] in ("COMPLETE", "INCOMPLETE")]
    out["wall_s_per_replayed_commit"] = {"median": statistics.median(walls) if walls else None, "max": max(walls) if walls else None, "total": round(sum(walls), 1)}
    out["incomplete_reasons"] = [x for r in rows for x in r.get("incomplete_reasons", [])]
    mpath = RES / "replay_phase4_mutants.jsonl"
    if mpath.exists():
        ms = list({m["id"]: m for m in (json.loads(line) for line in open(mpath))}.values())
        det = [m for m in ms if any(f["symbol"] == m["symbol"] for f in m["findings"])]
        out["mutants"] = {"detected": f"{len(det)}/{len(ms)}", "missed": [{"id": m["id"], "category": m["category"], "status": m["status"],
                                                                           "targets": [(t["target"], t["inputs"], t["classes"]) for t in m["targets"]]}
                                                                          for m in ms if m not in det]}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
