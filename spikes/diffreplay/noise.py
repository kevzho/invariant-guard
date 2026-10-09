"""Noise rate from the hand labels (labels.json) under three reporting policies.

usage: python noise.py  -> results/noise.json
"""
import json
from collections import Counter
from pathlib import Path

from analyze import changed_fn_view, load

HERE = Path(__file__).parent
labels = json.loads((HERE / "labels.json").read_text())
REPOS = ["more-itertools", "boltons", "dateutil"]

replayed = set()
for repo in REPOS:
    for r in load(repo):
        if any(a["executable"] for a in changed_fn_view(r).values()):
            replayed.add((repo, r["commit"][:8]))
bad_commits = {(l["repo"], l["commit"]) for l in labels if l["label"] == "UNINTENDED" and l["class"] != "exception_message"}
correct = replayed - bad_commits


def warn(l, policy):
    if policy == "P0_every_difference":
        return True
    if l["class"] == "exception_message":
        return False  # INFO
    ack_any = any(o.endswith("_new") for o in l["origins"])
    ack_all = all(o.endswith("_new") for o in l["origins"])
    if policy == "P1_ack_all_new":
        return not ack_all
    if policy == "P2_ack_any_new":
        return not ack_any
    raise ValueError(policy)


out = {"commits_with_executable_replay": len(replayed), "commits_with_probable_regression": sorted(bad_commits),
       "correct_commits_with_executable_replay": len(correct), "clusters": len(labels), "labels": dict(Counter(l["label"] for l in labels)),
       "policies": {}}
for pol in ("P0_every_difference", "P1_ack_all_new", "P2_ack_any_new"):
    w = [l for l in labels if warn(l, pol)]
    noisy_commits = {(l["repo"], l["commit"]) for l in w if (l["repo"], l["commit"]) in correct}
    artifact_commits = {(l["repo"], l["commit"]) for l in w if l["label"] in ("ARTIFACT",) or (l["label"] == "UNINTENDED" and l["class"] == "exception_message")}
    out["policies"][pol] = {
        "warn_clusters": len(w),
        "warn_cluster_labels": dict(Counter(l["label"] for l in w)),
        "correct_commits_with_warn": len(noisy_commits),
        "noise_rate_commits": f"{len(noisy_commits)}/{len(correct)} = {len(noisy_commits) / len(correct):.1%}",
        "commits_with_artifact_warn": len(artifact_commits),
        "true_positive_still_warned": any(l["label"] == "UNINTENDED" and l["class"] != "exception_message" for l in w),
        "noise_share_of_warn_clusters": f"{sum(1 for l in w if l['label'] != 'UNINTENDED' or l['class'] == 'exception_message')}/{len(w)}",
    }
intended = [l for l in labels if l["label"] in ("INTENDED", "INTENDED_CONSEQUENCE")]
out["intended_clusters_with_patch_test_witness"] = f"{sum(1 for l in intended if any(o.endswith('_new') for o in l['origins']))}/{len(intended)}"
(HERE / "results" / "noise.json").write_text(json.dumps(out, indent=1, default=list))
print(json.dumps(out, indent=1, default=list))
