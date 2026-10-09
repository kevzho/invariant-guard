"""Aggregate replay results; emit metrics (results/metrics.json) and the cases to label (results/diff_cases.md).

usage: python analyze.py
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from replay import DIFF_CLASSES

HERE = Path(__file__).parent
RES = HERE / "results"
REPOS = ["more-itertools", "boltons", "dateutil"]
NOT_EXEC = ("not_executable", "unknown", "no_baseline", "timeout_both")


def load(repo: str) -> list[dict]:
    p = RES / f"replay_{repo}.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def changed_fn_view(rec: dict) -> dict[str, dict]:
    """Aggregate sub-targets (direct / constructor / callers) per changed symbol."""
    out: dict[str, dict] = {}
    for f in rec.get("functions", []):
        a = out.setdefault(f["symbol"], {"symbol": f["symbol"], "doc_only": f["change"]["doc_only"], "kind": f["change"]["kind"],
                                         "signature_changed": f["signature_changed"], "targets": [], "classes": Counter(), "direct_classes": Counter(),
                                         "inputs_by_source": Counter(), "probe_ms": 0.0, "examples": defaultdict(list), "ext_encoded": 0})
        a["targets"].append((f["target"], f["relation"], f["inputs_total"]))
        a["classes"].update(f["classes"])
        if f["relation"] in ("direct", "constructor"):
            a["direct_classes"].update(f["classes"])
        a["inputs_by_source"].update(f["inputs_by_source"])
        a["probe_ms"] += f["probe_ms"]
        a["ext_encoded"] += f.get("ext_encoded", 0)
        for k, exs in f["examples"].items():
            for e in exs:
                if len(a["examples"][k]) < 6:
                    a["examples"][k].append({**e, "via": f["target"].split("::")[1], "relation": f["relation"]})
    for a in out.values():
        tot = sum(a["classes"].values())
        a["inputs"] = tot
        a["executable"] = tot - sum(a["classes"][k] for k in NOT_EXEC)
        a["direct_executable"] = sum(a["direct_classes"].values()) - sum(a["direct_classes"][k] for k in NOT_EXEC)
        a["differences"] = sum(a["classes"][k] for k in DIFF_CLASSES)
        a["nondeterministic"] = a["classes"]["nondeterministic"]
        a["internal_only"] = a["classes"]["internal_state_only"]
    return out


def main() -> None:
    metrics: dict = {"per_repo": {}, "totals": {}}
    cases = []
    all_fns = []
    for repo in REPOS:
        recs = load(repo)
        fns = []
        for r in recs:
            for sym, a in changed_fn_view(r).items():
                a["repo"], a["commit"], a["subject"] = repo, r["commit"], r.get("subject")
                fns.append(a)
        replayable = [a for a in fns]
        skipped = Counter(s["why"].split(" (")[0] for r in recs for s in r.get("skipped", []))
        src = Counter()
        for a in fns:
            src.update(a["inputs_by_source"])
        cls = Counter()
        for a in fns:
            cls.update(a["classes"])
        walls = [r.get("wall_s", 0) for r in recs if r.get("functions")]
        m = {
            "commits": len(recs),
            "commits_with_replayed_fn": sum(1 for r in recs if r.get("functions")),
            "commits_errors": sum(1 for r in recs if r.get("error")),
            "changed_fns_attempted": len(fns) + sum(skipped.values()),
            "changed_fns_skipped": dict(skipped),
            "changed_fns_replayed": len(fns),
            "fns_with_any_input": sum(1 for a in fns if a["inputs"] > 0),
            "fns_with_executable_input": sum(1 for a in fns if a["executable"] > 0),
            "fns_with_direct_executable_input": sum(1 for a in fns if a["direct_executable"] > 0),
            "fns_with_differences": sum(1 for a in fns if a["differences"] > 0),
            "fns_with_nondeterminism": sum(1 for a in fns if a["nondeterministic"] > 0),
            "fns_with_internal_only": sum(1 for a in fns if a["internal_only"] > 0 and a["differences"] == 0),
            "doc_only_fns": sum(1 for a in fns if a["doc_only"]),
            "doc_only_fns_with_differences": sum(1 for a in fns if a["doc_only"] and a["differences"] > 0),
            "inputs_total": sum(a["inputs"] for a in fns),
            "inputs_by_source": dict(src),
            "executable_inputs": sum(a["executable"] for a in fns),
            "classes": dict(cls),
            "wall_s_total": round(sum(r.get("wall_s", 0) for r in recs), 1),
            "wall_s_median_per_commit_with_fn": round(statistics.median(walls), 1) if walls else None,
            "probe_cpu_s_total": round(sum(a["probe_ms"] for a in fns) / 1000, 1),
            "t_prepare_s": round(sum(r.get("t_prepare_ms", 0) for r in recs) / 1000, 1),
            "t_generate_s": round(sum(r.get("t_generate_ms", 0) or 0 for r in recs) / 1000, 1),
            "t_replay_s": round(sum(r.get("t_replay_ms", 0) or 0 for r in recs) / 1000, 1),
            "ext_encoded_observations": sum(a["ext_encoded"] for a in fns),
        }
        metrics["per_repo"][repo] = m
        all_fns += fns
        for a in fns:
            if a["differences"] or a["nondeterministic"]:
                cases.append(a)
    tot: dict = {}
    for k in ("commits", "commits_with_replayed_fn", "changed_fns_replayed", "fns_with_any_input", "fns_with_executable_input",
              "fns_with_direct_executable_input", "fns_with_differences", "fns_with_nondeterminism", "fns_with_internal_only", "doc_only_fns",
              "doc_only_fns_with_differences", "inputs_total", "executable_inputs", "wall_s_total", "probe_cpu_s_total"):
        tot[k] = round(sum(metrics["per_repo"][r][k] for r in REPOS), 1)
    src = Counter()
    cls = Counter()
    for r in REPOS:
        src.update(metrics["per_repo"][r]["inputs_by_source"])
        cls.update(metrics["per_repo"][r]["classes"])
    tot["inputs_by_source"] = dict(src)
    tot["classes"] = dict(cls)
    # executable rate per source
    by_src_exec = Counter()
    by_src_all = Counter()
    for repo in REPOS:
        for r in load(repo):
            for f in r.get("functions", []):
                for s, c in f.get("classes_by_source", {}).items():
                    n = sum(c.values())
                    by_src_all[s] += n
                    by_src_exec[s] += n - sum(c.get(k, 0) for k in NOT_EXEC)
    tot["executable_rate_by_source"] = {s: f"{by_src_exec[s]}/{by_src_all[s]}" for s in sorted(by_src_all)}
    # commits with any observable difference
    tot["commits_with_any_difference"] = len({(a["repo"], a["commit"]) for a in all_fns if a["differences"]})
    tot["commits_with_executable_replay"] = len({(a["repo"], a["commit"]) for a in all_fns if a["executable"]})
    metrics["totals"] = tot
    (RES / "metrics.json").write_text(json.dumps(metrics, indent=1, default=list))
    lines = ["# Functions with differences or nondeterminism (to label)\n"]
    for i, a in enumerate(cases):
        lines.append(f"## C{i:02d} {a['repo']} {a['commit'][:8]} `{a['symbol']}`\n")
        lines.append(f"commit: {a['subject']}  \nclasses: {dict(a['classes'])}  \ntargets: {a['targets']}  doc_only={a['doc_only']} sig_changed={a['signature_changed']}\n")
        for k, exs in a["examples"].items():
            if k == "same":
                continue
            for e in exs[:3]:
                lines.append(f"- **{k}** [{e['origin']} via {e['via']}] `{e['input'][:220]}`  \n  base: `{e['base'][:260]}`  \n  patch: `{e['patch'][:260]}`"
                             + (f"  \n  base#2: `{e.get('base_repeat', '')[:200]}`" if e.get("base_repeat") else ""))
        lines.append("")
    (RES / "diff_cases.md").write_text("\n".join(lines))
    print(json.dumps(metrics["totals"], indent=1))
    print(len(cases), "cases written to results/diff_cases.md")


if __name__ == "__main__":
    main()
