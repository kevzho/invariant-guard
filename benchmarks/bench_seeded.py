"""Seeded realistic patches (docs/12 track 2) plus the solver, incremental and static/dynamic ablations.

For every case in benchmarks/manifests/seeded_patches.json (labels frozen before any tool run):

1. ground truth: the oracle (benchmarks/gt_oracle.py) executes the labeled witness and the finite
   grid on the base and the patched tree in a separate ``python -I`` process; the label is
   *confirmed* only if execution agrees with it (a disagreement is reported, never relabeled);
2. a throwaway Git repository (temporary directory) gets the base tree, a generated
   ``.guard/config.toml`` (declared domains, a docker run profile), a base commit, the case's
   contracts through the real ``guard contracts declare`` / ``discover`` / ``revise`` commands, and
   the patch overlay as uncommitted working-tree changes;
3. the real CLI runs ``guard verify --base <commit> --format json`` in fresh child processes:
   ``quick`` (solver, no cache), ``quick_nosolver``, ``full`` (no cache), ``quick_warm`` (cache
   populated by one earlier identical run) and, for cases with contracts, ``execute``
   (``--execute``, Docker runner).

Scoring (per run): a case is *flagged* when the report has a finding whose patch_relation is
INTRODUCED/CHANGED and whose result is PROVED_UNSAFE, POSSIBLY_UNSAFE, OBSERVED_REGRESSION or
OBSERVED_FAILURE; otherwise it *abstains* when required coverage is missing (run_outcome
INCOMPLETE, or the verdict is INCOMPLETE/ERROR), otherwise it is *clear*.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from bench_common import REPO, compact_report, flagged, git, guard, host_manifest, now, sha256_file, sha256_tree, write_result
from gt_oracle import compare

MANIFEST = REPO / "benchmarks" / "manifests" / "seeded_patches.json"
FIXTURES = REPO / "benchmarks" / "fixtures" / "seeded"
GITIGNORE = ".guard/cache/\n.guard/runs/\n.guard/artifacts/\n.guard/snapshots/\n.guard/state.sqlite*\n.guard/contracts/\n"


def config_for(case: dict[str, Any]) -> str:
    lines = ['schema_version = "1.0"', "", "[analysis]", "quick_budget_ms = 20000", "full_budget_ms = 120000", "",
             "[execution]", "enabled = true", 'profile = "iso"', "", "[execution.profiles.iso]", 'backend = "docker"', 'image = "python:3.13-slim"',
             'allowed_targets = ["app/*.py::*"]', ""]
    for d in case["domains"]:
        lines += ["[[domains]]", f'symbol = "{d["symbol"]}"', f'profile = "{d["profile"]}"', 'authority = "DECLARED"',
                  'reason = "benchmark: declared admitted inputs"', ""]
    return "\n".join(lines)


def overlay(dst: Path, src: Path) -> None:
    for p in src.rglob("*"):
        if p.is_file():
            t = dst / p.relative_to(src)
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, t)


def trees(case: dict[str, Any], tmp: Path) -> tuple[Path, Path]:
    base = FIXTURES / case["case_id"] / "base"
    patch_tree = tmp / "patch_tree"
    shutil.copytree(base, patch_tree)
    overlay(patch_tree, FIXTURES / case["case_id"] / "patch")
    return base, patch_tree


def ground_truth(case: dict[str, Any], base: Path, patch_tree: Path) -> dict[str, Any]:
    gt = case["ground_truth"]
    wit = compare(base, patch_tree, case["symbol"], gt["oracle"], gt["witness_inputs"]) if gt["witness_inputs"] else None
    grid = compare(base, patch_tree, case["symbol"], gt["oracle"], gt["grid_inputs"]) if gt["grid_inputs"] else None
    introduced = (wit or {}).get("introduced", 0) + (grid or {}).get("introduced", 0)
    pre = (wit or {}).get("pre_existing", 0) + (grid or {}).get("pre_existing", 0)
    label = gt["label"]
    if label == "defect_introduced":
        confirmed = bool(wit and wit["introduced"] > 0)
    elif label == "intentional_change":
        confirmed = bool(wit and wit["introduced"] > 0)  # the behavior really changes against the contract
    else:
        confirmed = introduced == 0
    undetermined = (wit or {}).get("undetermined", 0) + (grid or {}).get("undetermined", 0)
    return {"label": label, "intent": gt["intent"], "confirmed_by_execution": confirmed and undetermined == 0,
            "inputs_introduced": introduced, "inputs_pre_existing": pre, "inputs_undetermined": undetermined,
            "witness": wit, "grid": {k: v for k, v in (grid or {}).items() if k != "rows"} if grid else None,
            "grid_introducing_inputs": [r["input"] for r in (grid or {}).get("rows", []) if r["class"] == "introduced"][:5]}


def build_repo(case: dict[str, Any], tmp: Path) -> tuple[Path, str, list[dict[str, Any]]]:
    root = tmp / "repo"
    shutil.copytree(FIXTURES / case["case_id"] / "base", root)
    (root / ".guard").mkdir()
    (root / ".guard" / "config.toml").write_text(config_for(case), encoding="utf-8")
    (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    base = git(root, "rev-parse", "HEAD")
    setup = []
    for c in case["contracts"]:
        f = tmp / f"{c['contract_id']}.json"
        f.write_text(json.dumps(c), encoding="utf-8")
        code, out, err, ms = guard(["contracts", "declare", "--file", str(f), "--reason", "benchmark: maintainer-declared intended behavior",
                                    "--path", str(root)], cwd=root)
        setup.append({"step": f"declare {c['contract_id']}", "exit": code, "stderr_tail": err.strip().splitlines()[-1:] if err else []})
        if code != 0:
            raise RuntimeError(f"{case['case_id']}: declare failed: {err}")
    if case.get("discover_templates"):
        code, out, err, ms = guard(["contracts", "discover", "--path", str(root)], cwd=root)
        setup.append({"step": "discover (templates, recorded INFERRED)", "exit": code})
        if code != 0:
            raise RuntimeError(f"{case['case_id']}: discover failed: {err}")
    overlay(root, FIXTURES / case["case_id"] / "patch")
    if case.get("revise"):
        f = tmp / "revision.json"
        rev = dict(case["revise"])
        cid = rev.pop("contract_id")
        f.write_text(json.dumps(rev | {"contract_id": cid}), encoding="utf-8")
        code, out, err, ms = guard(["contracts", "revise", cid, "--file", str(f), "--reason", "benchmark: intentional API v2 (percentages)",
                                    "--path", str(root)], cwd=root)
        setup.append({"step": f"revise {cid}", "exit": code, "stderr_tail": err.strip().splitlines()[-1:] if err else []})
        if code != 0:
            raise RuntimeError(f"{case['case_id']}: revise failed: {err}")
    code, out, err, ms = guard(["contracts", "list", "--path", str(root), "--format", "json"], cwd=root)
    try:
        listed = json.loads(out)
    except json.JSONDecodeError:
        listed = {"raw": out[-500:], "exit": code}
    setup.append({"step": "contracts list", "exit": code, "contracts": listed})
    return root, base, setup


RUNS = {
    "quick": ["--mode", "quick", "--no-cache"],
    "quick_nosolver": ["--mode", "quick", "--no-cache", "--no-solver"],
    "full": ["--mode", "full", "--no-cache"],
    "execute": ["--mode", "quick", "--no-cache", "--execute"],
}


def verify(root: Path, base: str, extra: list[str]) -> dict[str, Any]:
    code, out, err, ms = guard(["verify", str(root), "--base", base, "--format", "json", *extra], cwd=root)
    try:
        rep = json.loads(out)
    except json.JSONDecodeError:
        return {"exit": code, "wall_ms": ms, "error": "no JSON report", "stderr_tail": err[-800:]}
    return {"exit": code, "wall_ms": ms, **compact_report(rep)}


def predict(run: dict[str, Any], kind: str = "any") -> str:
    """flag / abstain / clear / error for one engine subset of one run."""
    if "error" in run:
        return "error"
    if flagged(run["findings"], kind):
        return "flag"
    if run["verdict"] == "ERROR":
        return "error"
    if kind == "dynamic":
        cc = run.get("contract_coverage") or {}
        if not run["contract_results"]:
            return "n/a"
        if (cc.get("resolved_required_contract_checks") or 0) < (cc.get("required_contract_checks") or 0):
            return "abstain"
        if any(c["property_result"] in ("UNKNOWN", "UNVERIFIED") for c in run["contract_results"]):
            return "abstain"
        return "clear"
    if kind == "static":
        return "abstain" if run["unknown_required"] or run["skipped_checks"] else "clear"
    return "abstain" if run["run_outcome"] != "COMPLETE" or run["verdict"] == "INCOMPLETE" else "clear"


def localized(run: dict[str, Any], symbol: str) -> bool:
    for f in flagged(run.get("findings", [])):
        scope = (f.get("contract") or {}).get("scope") if isinstance(f.get("contract"), dict) else None
        if f.get("symbol_id") == symbol or scope == symbol:
            return True
    return False


def obligation_mismatch(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Compare obligations reported by run a (e.g. quick) with the same IDs in run b (e.g. full)."""
    oa, ob = a.get("obligations", {}), b.get("obligations", {})
    same = diff = missing = 0
    diffs = []
    for oid, ra in oa.items():
        rb = ob.get(oid)
        if rb is None:
            missing += 1
            continue
        if (ra["result"], ra["scope"]) == (rb["result"], rb["scope"]):
            same += 1
        else:
            diff += 1
            diffs.append({"obligation_id": oid, "a": [ra["result"], ra["scope"]], "b": [rb["result"], rb["scope"]]})
    fa = {(f["obligation_id"], f["patch_relation"]) for f in flagged(a.get("findings", []), "static")}
    fb = {(f["obligation_id"], f["patch_relation"]) for f in flagged(b.get("findings", []), "static")}
    return {"compared": same + diff, "equal": same, "mismatched": diff, "missing_in_b": missing, "mismatches": diffs,
            "flagged_only_in_a": sorted(map(list, fa - fb)), "flagged_only_in_b": sorted(map(list, fb - fa))}


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix=f"ig-seeded-{case['case_id']}-"))
    try:
        base_tree, patch_tree = trees(case, tmp)
        gt = ground_truth(case, base_tree, patch_tree)
        root, base, setup = build_repo(case, tmp)
        runs = {name: verify(root, base, extra) for name, extra in RUNS.items()
                if name != "execute" or case["contracts"] or case.get("discover_templates")}
        verify(root, base, ["--mode", "quick"])  # populate the summary cache
        runs["quick_warm"] = verify(root, base, ["--mode", "quick"])
        preds = {name: predict(r) for name, r in runs.items()}
        engines = {"static_only": predict(runs["quick"])}  # the run without --execute: contracts are NOT_RUN and cannot flag
        if "execute" in runs:
            engines["dynamic_only"] = predict(runs["execute"], "dynamic")
            engines["combined"] = predict(runs["execute"], "any")
        return {"case_id": case["case_id"], "class": case["class"], "symbol": case["symbol"], "property": case["property"],
                "anticipated_tool_outcome": case["anticipated_tool_outcome"], "ground_truth": gt, "setup": setup, "runs": runs,
                "prediction": preds, "engines": engines, "localized": {n: localized(r, case["symbol"]) for n, r in runs.items()},
                "incremental_vs_full": obligation_mismatch(runs["quick"], runs["full"]),
                "warm_vs_full": obligation_mismatch(runs["quick_warm"], runs["full"]),
                "nosolver_vs_solver": obligation_mismatch(runs["quick_nosolver"], runs["quick"])}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def confusion(rows: list[dict[str, Any]], pred_of) -> dict[str, Any]:
    """TP/FP/FN/TN/abstain over confirmed labels; abstentions stay in the denominator."""
    c = {"TP": 0, "FN": 0, "abstain_pos": 0, "FP": 0, "TN": 0, "abstain_neg": 0, "error": 0, "n_pos": 0, "n_neg": 0}
    for r in rows:
        lab = r["ground_truth"]["label"]
        if lab not in ("defect_introduced", "no_defect_introduced") or not r["ground_truth"]["confirmed_by_execution"]:
            continue
        p = pred_of(r)
        if p is None or p == "n/a":
            continue
        pos = lab == "defect_introduced"
        c["n_pos" if pos else "n_neg"] += 1
        if p == "error":
            c["error"] += 1
        elif p == "flag":
            c["TP" if pos else "FP"] += 1
        elif p == "clear":
            c["FN" if pos else "TN"] += 1
        else:
            c["abstain_pos" if pos else "abstain_neg"] += 1
    n = c["n_pos"] + c["n_neg"]
    c["precision"] = round(c["TP"] / (c["TP"] + c["FP"]), 4) if c["TP"] + c["FP"] else None
    c["recall_abstain_as_miss"] = round(c["TP"] / c["n_pos"], 4) if c["n_pos"] else None
    c["recall_decided_only"] = round(c["TP"] / (c["TP"] + c["FN"]), 4) if c["TP"] + c["FN"] else None
    c["abstention_rate"] = round((c["abstain_pos"] + c["abstain_neg"]) / n, 4) if n else None
    c["false_positive_rate"] = round(c["FP"] / c["n_neg"], 4) if c["n_neg"] else None
    return c


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"cases": len(rows), "labels": {}, "label_disagreements": []}
    for r in rows:
        out["labels"][r["ground_truth"]["label"]] = out["labels"].get(r["ground_truth"]["label"], 0) + 1
        if not r["ground_truth"]["confirmed_by_execution"]:
            out["label_disagreements"].append(r["case_id"])
    out["overall"] = {name: confusion(rows, lambda r, n=name: r["prediction"].get(n)) for name in ("quick", "quick_nosolver", "full", "quick_warm")}
    contract_rows = [r for r in rows if "execute" in r["runs"]]
    out["contract_cases"] = [r["case_id"] for r in contract_rows]
    out["engines_on_contract_cases"] = {e: confusion(contract_rows, lambda r, e=e: r["engines"].get(e)) for e in ("static_only", "dynamic_only", "combined")}
    by_class: dict[str, Any] = {}
    for r in rows:
        by_class.setdefault(r["class"], []).append(r)
    out["by_class"] = {k: {"cases": [r["case_id"] for r in v], "label": sorted({r["ground_truth"]["label"] for r in v}),
                           "quick": [r["prediction"]["quick"] for r in v], "best_available": [r["prediction"].get("execute", r["prediction"]["quick"]) for r in v],
                           "verdicts_quick": [r["runs"]["quick"].get("verdict") for r in v],
                           "verdicts_execute": [r["runs"].get("execute", {}).get("verdict") for r in v]} for k, v in by_class.items()}
    out["best_available"] = confusion(rows, lambda r: r["prediction"].get("execute", r["prediction"]["quick"]))
    intentional = [r for r in rows if r["ground_truth"]["intent"] == "intentional"]
    out["intentional_changes"] = [{"case_id": r["case_id"], "label": r["ground_truth"]["label"], "verdict_quick": r["runs"]["quick"].get("verdict"),
                                   "verdict_execute": r["runs"].get("execute", {}).get("verdict"),
                                   "blocked": "BLOCK" in (r["runs"]["quick"].get("verdict"), r["runs"].get("execute", {}).get("verdict"))} for r in intentional]
    out["blocks_on_negatives"] = [r["case_id"] for r in rows if r["ground_truth"]["label"] == "no_defect_introduced"
                                  and "BLOCK" in (r["runs"]["quick"].get("verdict"), r["runs"].get("execute", {}).get("verdict"))]
    mism = [r["incremental_vs_full"] for r in rows]
    warm = [r["warm_vs_full"] for r in rows]
    out["incremental_vs_full"] = {"cases": len(rows), "obligations_compared": sum(m["compared"] for m in mism),
                                  "mismatched": sum(m["mismatched"] for m in mism), "flagged_only_in_full": sum(len(m["flagged_only_in_b"]) for m in mism),
                                  "flagged_only_in_quick": sum(len(m["flagged_only_in_a"]) for m in mism),
                                  "warm_obligations_compared": sum(m["compared"] for m in warm), "warm_mismatched": sum(m["mismatched"] for m in warm),
                                  "warm_flagged_only_in_full": sum(len(m["flagged_only_in_b"]) for m in warm),
                                  "verdict_mismatch_quick_vs_full": [r["case_id"] for r in rows if r["runs"]["quick"].get("verdict") != r["runs"]["full"].get("verdict")],
                                  "wall_ms": {n: sum(r["runs"][n]["wall_ms"] for r in rows) for n in ("quick", "full", "quick_warm", "quick_nosolver")}}
    ns = [r["nosolver_vs_solver"] for r in rows]
    out["solver_ablation"] = {"obligations_compared": sum(m["compared"] for m in ns), "mismatched": sum(m["mismatched"] for m in ns),
                              "changes": [{"case_id": r["case_id"], **d} for r in rows for d in r["nosolver_vs_solver"]["mismatches"]],
                              "verdicts_changed": [{"case_id": r["case_id"], "solver": r["runs"]["quick"].get("verdict"),
                                                    "no_solver": r["runs"]["quick_nosolver"].get("verdict")} for r in rows
                                                   if r["runs"]["quick"].get("verdict") != r["runs"]["quick_nosolver"].get("verdict")]}
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="", help="comma-separated case IDs (default: all)")
    ap.add_argument("--out-name", default="paper_seeded_patches")
    args = ap.parse_args(argv)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sel = [c for c in manifest["cases"] if not args.cases or c["case_id"] in args.cases.split(",")]
    doc: dict[str, Any] = {"schema_version": "1.0", "benchmark": "seeded_patches", "started_at": now(), "host": host_manifest(),
                           "manifest": {"path": str(MANIFEST.relative_to(REPO)), "sha256": sha256_file(MANIFEST),
                                        "labels_frozen_at": manifest["labels_frozen_at"], "fixtures_sha256": sha256_tree(FIXTURES)},
                           "definitions": __doc__, "runs": {k: ["guard", "verify", "ROOT", "--base", "COMMIT", "--format", "json", *v] for k, v in RUNS.items()}}
    rows = []
    for c in sel:
        r = run_case(c)
        rows.append(r)
        print(f"{c['case_id']:<4} {c['class']:<34} gt={r['ground_truth']['label']}{'' if r['ground_truth']['confirmed_by_execution'] else '(UNCONFIRMED)'} "
              f"quick={r['prediction']['quick']}/{r['runs']['quick'].get('verdict')} exec={r['prediction'].get('execute', '-')}/"
              f"{r['runs'].get('execute', {}).get('verdict', '-')} nosolver={r['prediction']['quick_nosolver']}", file=sys.stderr, flush=True)
    doc["cases"] = rows
    doc["summary"] = summarize(rows)
    doc["finished_at"] = now()
    out = write_result(args.out_name, doc)
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
