"""Controlled semantic fixture suite (docs/12 track 1) with and without the solver.

For every case of tests/semantic_fixtures/manifest.json the real CLI runs
``guard check <file> --format json`` (and ``--no-solver``) in a fresh child process and the
case's obligation result is compared with

* the manifest's *expected* result (the specification the tests check), and
* the independent ground truth in benchmarks/manifests/semantic_ground_truth.json: a witness
  executed in a separate ``python -I`` process (benchmarks/gt_oracle.py) for defects, a hand
  argument corroborated by a finite grid for safe cases, and no label for indeterminate cases.

Prediction: PROVED_UNSAFE / POSSIBLY_UNSAFE = flag, PROVED_SAFE = clear, UNKNOWN = abstain.
Precision/recall use labeled cases only; abstentions stay in the denominator.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from bench_common import REPO, guard, host_manifest, now, sha256_file, write_result
from gt_oracle import execute

TEST_MANIFEST = REPO / "tests" / "semantic_fixtures" / "manifest.json"
GT_MANIFEST = REPO / "benchmarks" / "manifests" / "semantic_ground_truth.json"
DEMO = REPO / "examples" / "python_demo"
RESULTS = ("PROVED_SAFE", "PROVED_UNSAFE", "POSSIBLY_UNSAFE", "UNKNOWN")


def check(path: str, extra: list[str]) -> tuple[int, dict[str, Any], float]:
    code, out, err, ms = guard(["check", str(REPO / path), "--format", "json", *extra], cwd=REPO)
    return code, json.loads(out), ms


def pred(result: str | None) -> str:
    return {"PROVED_UNSAFE": "flag", "POSSIBLY_UNSAFE": "flag", "PROVED_SAFE": "clear"}.get(result or "", "abstain")


def oracle_case(case: dict[str, Any], gt: dict[str, Any]) -> dict[str, Any]:
    if gt["label"] not in ("defect", "safe"):
        return {"label": gt["label"], "confirmed_by_execution": None, "note": gt["note"]}
    sym = case["symbol_id"]
    tree = DEMO / sym.split("/")[0]
    rel_symbol = sym.split("/", 1)[1]
    if gt["label"] == "defect":
        w = gt["witness"]
        res = execute(tree, rel_symbol, [w], optimize=gt.get("optimize", False), construct=gt.get("construct"))
        ok = res[0]["kind"] == "raised" and res[0]["exception"] == gt["failure_exception"]
        out = {"label": "defect", "confirmed_by_execution": ok, "witness": w, "observed": res[0], "python_flags": "-I -O" if gt.get("optimize") else "-I"}
        if gt.get("optimize"):
            plain = execute(tree, rel_symbol, [w], construct=gt.get("construct"))
            out["observed_without_O"] = plain[0]
        return out
    res = execute(tree, rel_symbol, gt["grid_inputs"], construct=gt.get("construct")) if gt["grid_inputs"] else []
    bad = [r for r in res if r["kind"] == "raised" and r["exception"] == gt["failure_exception"]
           and (gt.get("site_line") is None or r.get("line") == gt["site_line"])]
    errs = [r for r in res if r["kind"] == "harness_error"]
    return {"label": "safe", "confirmed_by_execution": not bad and not errs, "method": gt["method"], "grid_size": len(res),
            "grid_failures": len(bad), "site_line": gt.get("site_line"), "note": gt["note"]}


def confusion(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    c = {"TP": 0, "FN": 0, "abstain_pos": 0, "FP": 0, "TN": 0, "abstain_neg": 0, "n_pos": 0, "n_neg": 0, "unlabeled": 0}
    for r in rows:
        g = r["ground_truth"]
        if g["label"] not in ("defect", "safe") or not g["confirmed_by_execution"]:
            c["unlabeled"] += 1
            continue
        p = pred(r[key]["property_result"])
        pos = g["label"] == "defect"
        c["n_pos" if pos else "n_neg"] += 1
        if p == "flag":
            c["TP" if pos else "FP"] += 1
        elif p == "clear":
            c["FN" if pos else "TN"] += 1
        else:
            c["abstain_pos" if pos else "abstain_neg"] += 1
    n = c["n_pos"] + c["n_neg"]
    c["precision"] = round(c["TP"] / (c["TP"] + c["FP"]), 4) if c["TP"] + c["FP"] else None
    c["recall_abstain_as_miss"] = round(c["TP"] / c["n_pos"], 4) if c["n_pos"] else None
    c["recall_decided_only"] = round(c["TP"] / (c["TP"] + c["FN"]), 4) if c["TP"] + c["FN"] else None
    c["safe_proof_rate"] = round(c["TN"] / c["n_neg"], 4) if c["n_neg"] else None
    c["abstention_rate_labeled"] = round((c["abstain_pos"] + c["abstain_neg"]) / n, 4) if n else None
    c["abstention_rate_all_cases"] = round(sum(1 for r in rows if pred(r[key]["property_result"]) == "abstain") / len(rows), 4)
    return c


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--out-name", default="paper_semantic_fixtures")
    args = ap.parse_args(argv)
    m = json.loads(TEST_MANIFEST.read_text(encoding="utf-8"))
    gtm = json.loads(GT_MANIFEST.read_text(encoding="utf-8"))["cases"]
    cache: dict[tuple[str, bool], tuple[int, dict[str, Any], float]] = {}
    rows = []
    for case in m["cases"]:
        row: dict[str, Any] = {"case_id": case["case_id"], "path": case["path"], "symbol_id": case["symbol_id"], "property": case["property"]}
        for solver in (True, False):
            k = (case["path"], solver)
            if k not in cache:
                cache[k] = check(case["path"], [] if solver else ["--no-solver"])
            code, rep, ms = cache[k]
            oid = f"{case['symbol_id']}::{case['property']}#{case['obligation_ordinal']}"
            r = next((x for x in rep["obligation_results"] if x["obligation_id"] == oid), None)
            exp = case["expected"] if solver else case["expected_without_solver"]
            key = "solver" if solver else "no_solver"
            row[key] = {"property_result": r and r["property_result"], "scope_validity": r and r["scope_validity"], "method": r and r.get("method"),
                        "witness": r and r.get("witness"), "witness_validated": r and r.get("witness_validated"),
                        "expected_property_result": exp["property_result"], "expected_scope_validity": exp["scope_validity"],
                        "matches_expected": bool(r) and r["property_result"] == exp["property_result"] and r["scope_validity"] == exp["scope_validity"],
                        "file_verdict": rep["verdict"], "file_run_outcome": rep["run_outcome"], "file_exit": code, "file_wall_ms": ms,
                        "solver_ms": (rep.get("timing_ms") or {}).get("solver"), "reasons": (r or {}).get("reasons", [])[:2]}
        row["ground_truth"] = oracle_case(case, gtm[case["case_id"]])
        rows.append(row)
        print(f"{case['case_id']:<32} solver={row['solver']['property_result']:<15} nosolver={row['no_solver']['property_result']:<15} "
              f"gt={row['ground_truth']['label']}:{row['ground_truth']['confirmed_by_execution']}", file=sys.stderr, flush=True)
    skips = []
    for s in m["skip_cases"]:
        code, out, err, ms = guard(["check", str(REPO / s["path"]), "--format", "json"], cwd=REPO)
        rep = json.loads(out)
        skips.append({"case_id": s["case_id"], "expected_run_outcome": s["expected_run_outcome"], "run_outcome": rep["run_outcome"],
                      "verdict": rep["verdict"], "exit": code, "skipped": [k["kind"] for k in rep["coverage"]["skipped_checks"]]})
    summary = {}
    for key in ("solver", "no_solver"):
        summary[key] = {"result_counts": dict(Counter(r[key]["property_result"] for r in rows)),
                        "matches_expected": sum(r[key]["matches_expected"] for r in rows), "cases": len(rows),
                        "confusion": confusion(rows, key)}
    summary["ground_truth"] = dict(Counter(f"{r['ground_truth']['label']}:{r['ground_truth']['confirmed_by_execution']}" for r in rows))
    summary["solver_changed"] = [{"case_id": r["case_id"], "solver": r["solver"]["property_result"], "no_solver": r["no_solver"]["property_result"]}
                                 for r in rows if r["solver"]["property_result"] != r["no_solver"]["property_result"]]
    doc = {"schema_version": "1.0", "benchmark": "semantic_fixtures", "started_at": now(), "host": host_manifest(),
           "manifests": {"tests": {"path": str(TEST_MANIFEST.relative_to(REPO)), "sha256": sha256_file(TEST_MANIFEST)},
                         "ground_truth": {"path": str(GT_MANIFEST.relative_to(REPO)), "sha256": sha256_file(GT_MANIFEST)}},
           "config": {"path": "examples/python_demo/.guard/config.toml", "sha256": sha256_file(DEMO / ".guard" / "config.toml")},
           "definitions": __doc__, "cases": rows, "skip_cases": skips, "summary": summary, "finished_at": now()}
    out = write_result(args.out_name, doc)
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
