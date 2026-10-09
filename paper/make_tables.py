"""Generate every table, figure and in-text number of the paper from benchmarks/results/paper_*.json.

    .venv/bin/python paper/make_tables.py

Writes paper/generated/{numbers.tex, tab_*.tex, fig_*.pdf}. Nothing is typed by hand: the paper
text uses the macros in numbers.tex, and the tables are \\input from this directory.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
RES = REPO / "benchmarks" / "results"
OUT = REPO / "paper" / "generated"

MACROS: dict[str, str] = {}


def load(name: str) -> dict[str, Any]:
    return json.loads((RES / f"{name}.json").read_text(encoding="utf-8"))


def tex(s: Any) -> str:
    s = "--" if s is None else str(s)
    for a, b in (("_", r"\_"), ("%", r"\%"), ("&", r"\&"), ("#", r"\#"), ("$", r"\$")):
        s = s.replace(a, b)
    return s


def macro(name: str, value: Any) -> None:
    assert name.isalpha(), name
    MACROS[name] = tex(value)


def fmt(x: float | None, nd: int = 2) -> str:
    return "--" if x is None else f"{x:.{nd}f}"


def ms(x: float | None) -> str:
    if x is None:
        return "--"
    return f"{x:,.0f}".replace(",", "{,}")


def write(name: str, body: str) -> None:
    (OUT / name).write_text(body, encoding="utf-8")


def table(cols: str, header: list[str], rows: list[list[Any]], caption: str, label: str, small: bool = True, star: bool = False,
          size: str | None = None, colsep: str | None = None, placement: str = "t") -> str:
    env = "table*" if star else "table"
    lines = [rf"\begin{{{env}}}[{placement}]", r"\centering", size or (r"\small" if small else ""), rf"\setlength{{\tabcolsep}}{{{colsep}}}" if colsep else "",
             rf"\caption{{{caption}}}", rf"\label{{{label}}}",
             rf"\begin{{tabular}}{{{cols}}}", r"\toprule", " & ".join(header) + r" \\", r"\midrule"]
    for r in rows:
        if r == ["MID"]:
            lines.append(r"\midrule")
        else:
            lines.append(" & ".join(str(c) for c in r) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}", rf"\end{{{env}}}", ""]
    return "\n".join(lines)


# ----------------------------------------------------------------------------------- semantic fixtures
def semantic() -> None:
    d = load("paper_semantic_fixtures")
    s = d["summary"]
    rows = []
    for key, name in (("solver", "solver on"), ("no_solver", "solver off")):
        rc, c = s[key]["result_counts"], s[key]["confusion"]
        rows.append([name, rc.get("PROVED_SAFE", 0), rc.get("PROVED_UNSAFE", 0), rc.get("POSSIBLY_UNSAFE", 0), rc.get("UNKNOWN", 0),
                     f"{s[key]['matches_expected']}/{s[key]['cases']}", c["TP"], c["FP"], c["FN"], c["TN"], c["abstain_pos"] + c["abstain_neg"],
                     fmt(c["precision"]), fmt(c["recall_abstain_as_miss"]), fmt(c["recall_decided_only"]), fmt(c["abstention_rate_labeled"])])
    write("tab_semantic.tex", table("lrrrrrrrrrrrrrr",
          ["", "SAFE", "UNSAFE", "POSS.", "UNK.", "=spec", "TP", "FP", "FN", "TN", "Abst.", "Prec.", r"Rec.\textsubscript{a}", r"Rec.\textsubscript{d}", "Abst.\\,rate"],
          rows, "Controlled semantic fixtures (one obligation per case). Result counts over all cases; ``=spec'' is agreement with the "
          "specified expected result; the confusion counts use only cases with an independent label (defect: witness executed; safe: hand "
          r"argument plus finite grid). Rec.\textsubscript{a} counts abstentions as misses; Rec.\textsubscript{d} uses decided cases only.",
          "tab:semantic", star=True))
    c = s["solver"]["confusion"]
    cn = s["no_solver"]["confusion"]
    macro("SemCases", s["solver"]["cases"])
    macro("SemLabeled", c["n_pos"] + c["n_neg"])
    macro("SemPos", c["n_pos"])
    macro("SemNeg", c["n_neg"])
    macro("SemUnlabeled", c["unlabeled"])
    macro("SemTP", c["TP"])
    macro("SemFP", c["FP"])
    macro("SemFN", c["FN"])
    macro("SemTN", c["TN"])
    macro("SemAbstainPos", c["abstain_pos"])
    macro("SemAbstainNeg", c["abstain_neg"])
    macro("SemPrecision", fmt(c["precision"]))
    macro("SemRecallA", fmt(c["recall_abstain_as_miss"]))
    macro("SemRecallD", fmt(c["recall_decided_only"]))
    macro("SemAbstRate", fmt(c["abstention_rate_labeled"]))
    macro("SemMatch", s["solver"]["matches_expected"])
    macro("SemMatchNoSolver", s["no_solver"]["matches_expected"])
    macro("SemNoSolverTP", cn["TP"])
    macro("SemNoSolverTN", cn["TN"])
    macro("SemNoSolverAbst", cn["abstain_pos"] + cn["abstain_neg"])
    macro("SemSolverChanged", len(s["solver_changed"]))
    macro("SemSolverChangedList", ", ".join(x["case_id"] for x in s["solver_changed"]))
    gt = Counter(r["ground_truth"]["label"] for r in d["cases"])
    macro("SemIndeterminate", gt.get("indeterminate", 0) + gt.get("excluded", 0))
    confirmed = sum(1 for r in d["cases"] if r["ground_truth"].get("confirmed_by_execution"))
    macro("SemConfirmed", confirmed)


# ----------------------------------------------------------------------------------- seeded patches
CLASS_NAMES = {
    "div_zero_guard_removed": "div. guard removed", "div_zero_expression_changed": "div. expression changed", "div_zero_guard_weakened": "div. guard weakened",
    "div_zero_guard_reordered": "div. guard reordered", "changed_callee": "changed callee", "changed_constant": "changed constant",
    "index_guard_removed": "index guard removed", "index_off_by_one": "index off by one", "none_guard_removed": "None guard removed",
    "none_from_callee": "None from callee", "contract_regression_normalization": "contract: normalization", "contract_regression_purity": "contract: purity",
    "contract_regression_length": "contract: length", "contract_regression_range": "contract: range", "contract_and_static_div_zero": "contract + static div.",
    "intentional_change_revised": "intentional, revised", "intentional_change_inferred": "intentional, inferred", "intentional_change_static": "intentional, static",
    "intentional_change_unrevised": "intentional, unrevised", "safe_refactor": "safe refactor", "safe_refactor_contract": "safe refactor (contract)",
    "unsupported_loop": "unsupported: loop", "unsupported_nested_function": "unsupported: nested def", "unsupported_external_call": "unsupported: ext. call",
    "pre_existing_static": "pre-existing (static)", "pre_existing_contract": "pre-existing (contract)",
}
GROUPS = [("division by zero", ("S01", "S02", "S03", "S04", "S05", "S08")), ("changed callee", ("S06", "S07")),
          ("index bounds", ("S09", "S10", "S11")), ("None flow", ("S12", "S13")), ("contract regression", ("D01", "D02", "D03", "D04", "D05")),
          ("unsupported construct", ("U01", "U02", "U03", "U04")), ("safe refactor", ("N01", "N02", "N03", "N04", "N05", "N06", "N07")),
          ("intentional change", ("I01", "I02", "I03", "I04")), ("pre-existing failure", ("P01", "P02"))]
LABEL_SHORT = {"defect_introduced": "defect", "no_defect_introduced": "none", "intentional_change": "intent."}
PRED_SHORT = {"flag": "F", "clear": "C", "abstain": "A", "error": "E", "n/a": "--", None: "--"}


def seeded() -> None:
    d = load("paper_seeded_patches")
    s = d["summary"]
    rows = []
    for r in d["cases"]:
        ex = r["runs"].get("execute")
        rows.append([r["case_id"], CLASS_NAMES.get(r["class"], r["class"]), LABEL_SHORT[r["ground_truth"]["label"]] + ("" if r["ground_truth"]["confirmed_by_execution"] else "?"),
                     f"{PRED_SHORT[r['prediction']['quick']]}/{tex(r['runs']['quick'].get('verdict'))}",
                     PRED_SHORT[r["prediction"]["quick_nosolver"]], f"{PRED_SHORT[r['prediction']['full']]}/{tex(r['runs']['full'].get('verdict'))}",
                     f"{PRED_SHORT[r['prediction']['execute']]}/{tex(ex.get('verdict'))}" if ex else "--"])
    write("tab_seeded_cases.tex", table("llllllll"[:7], ["ID", "class", "label", "quick", "no-solver", "full", "execute"], rows,
          "Seeded baseline/patch pairs, one row per case (labels frozen before any tool run; ``?'' = execution did not confirm the label). "
          "Predictions: F = flagged (an INTRODUCED/CHANGED failure finding), C = clear, A = abstained (required coverage missing); "
          "the verdict follows the slash. \\emph{execute} adds dynamic contract checks in the Docker runner (cases with contracts only).",
          "tab:seeded-cases", star=True, placement="h!"))
    rows = []
    for name, label in (("quick", "static quick"), ("quick_nosolver", "static quick, no solver"), ("full", "static full"), ("quick_warm", "static quick, warm cache")):
        c = s["overall"][name]
        rows.append([label, c["n_pos"], c["n_neg"], c["TP"], c["FN"], c["abstain_pos"], c["FP"], c["TN"], c["abstain_neg"],
                     fmt(c["precision"]), fmt(c["recall_abstain_as_miss"]), fmt(c["recall_decided_only"]), fmt(c["abstention_rate"])])
    c = s["best_available"]
    rows.append(["MID"])
    rows.append(["static + dynamic (execute)", c["n_pos"], c["n_neg"], c["TP"], c["FN"], c["abstain_pos"], c["FP"], c["TN"], c["abstain_neg"],
                 fmt(c["precision"]), fmt(c["recall_abstain_as_miss"]), fmt(c["recall_decided_only"]), fmt(c["abstention_rate"])])
    write("tab_seeded_confusion.tex", table("lrrrrrrrrrrrr", ["configuration", "pos", "neg", "TP", "FN", r"A\textsubscript{pos}", "FP", "TN",
                                                              r"A\textsubscript{neg}", "Prec.", r"Rec.\textsubscript{a}", r"Rec.\textsubscript{d}", "Abst."], rows,
          "Seeded patches: defect detection over execution-confirmed labels (intentional changes excluded; see text). "
          r"A = abstentions, kept in the denominators; Rec.\textsubscript{a} counts them as misses. The last row uses the \emph{execute} run for "
          "cases with contracts and the static quick run otherwise.", "tab:seeded-confusion", star=True))
    rows = []
    e = s["engines_on_contract_cases"]
    for k, label in (("static_only", "static only"), ("dynamic_only", "dynamic only"), ("combined", "combined")):
        c = e[k]
        rows.append([label, c["n_pos"] + c["n_neg"], c["TP"], c["FN"], c["abstain_pos"], c["FP"], c["TN"], c["abstain_neg"]])
    write("tab_engines.tex", table("lrrrrrrr", ["engines", "cases", "TP", "FN", r"A\textsubscript{pos}", "FP", "TN", r"A\textsubscript{neg}"], rows,
          f"Static-only, dynamic-only and combined verification on the {len(s['contract_cases'])} seeded cases that carry contracts "
          "(labels confirmed by execution; intentional changes excluded). \\emph{Static only}: the quick run without \\texttt{-{}-execute}; "
          "\\emph{dynamic only}: the contract results of the \\texttt{-{}-execute} run; \\emph{combined}: that run's verdict.", "tab:engines",
          size=r"\footnotesize", colsep="4pt"))
    # per-class confusion (docs/12: per failure class, denominators, abstentions kept)
    rows = []
    for gname, ids in GROUPS:
        rs = [r for r in d["cases"] if r["case_id"] in ids]
        labs = Counter(LABEL_SHORT[r["ground_truth"]["label"]] for r in rs)

        def conf(pred_of):
            c = Counter()
            for r in rs:
                lab = r["ground_truth"]["label"]
                if lab == "intentional_change" or not r["ground_truth"]["confirmed_by_execution"]:
                    continue
                pr = pred_of(r)
                pos = lab == "defect_introduced"
                c[{("flag", True): "TP", ("flag", False): "FP", ("clear", True): "FN", ("clear", False): "TN"}.get((pr, pos), "A")] += 1
            return c
        q = conf(lambda r: r["prediction"]["quick"])
        b = conf(lambda r: r["prediction"].get("execute", r["prediction"]["quick"]))
        rows.append([gname, len(rs), ", ".join(f"{v} {k}" for k, v in sorted(labs.items())), q["TP"], q["FN"], q["A"], q["FP"], q["TN"],
                     b["TP"], b["FN"], b["A"], b["FP"], b["TN"]])
    tot = [sum(r[i] for r in rows) if isinstance(rows[0][i], int) else "" for i in range(len(rows[0]))]
    tot[0], tot[2] = "all", f"{s['labels'].get('defect_introduced', 0)} defect, {s['labels'].get('no_defect_introduced', 0)} none, {s['labels'].get('intentional_change', 0)} intent."
    rows += [["MID"], tot]
    write("tab_seeded_classes.tex", table("lrlrrrrrrrrrr", ["class", "n", "labels", "TP", "FN", "A", "FP", "TN", "TP", "FN", "A", "FP", "TN"], rows,
          "Seeded patches by class. Left block: static quick verification (\\texttt{guard verify -{}-mode quick}); right block: the best available "
          "configuration (the \\texttt{-{}-execute} run for cases with contracts, otherwise static quick). A = abstained (required coverage "
          "missing: verdict INCOMPLETE without a flagged finding), kept in every denominator. The two \\emph{intentional} labels are excluded "
          "from the counts and discussed in the text. Labels confirmed by executing both versions in a separate interpreter.",
          "tab:seeded-classes", star=True).replace(r"\toprule", r"\toprule" + "\n & & & \\multicolumn{5}{c}{static quick} & \\multicolumn{5}{c}{best available} \\\\ "
                                                     "\\cmidrule(lr){4-8}\\cmidrule(lr){9-13}", 1))
    ov = s["overall"]["quick"]
    ba = s["best_available"]
    macro("SeedCases", s["cases"])
    macro("SeedPos", ov["n_pos"])
    macro("SeedNeg", ov["n_neg"])
    macro("SeedIntentional", s["labels"].get("intentional_change", 0))
    macro("SeedDisagree", len(s["label_disagreements"]))
    macro("SeedQuickTP", ov["TP"])
    macro("SeedQuickFN", ov["FN"])
    macro("SeedQuickAbstPos", ov["abstain_pos"])
    macro("SeedQuickFP", ov["FP"])
    macro("SeedQuickTN", ov["TN"])
    macro("SeedQuickAbstNeg", ov["abstain_neg"])
    macro("SeedQuickPrec", fmt(ov["precision"]))
    macro("SeedQuickRecA", fmt(ov["recall_abstain_as_miss"]))
    macro("SeedQuickRecD", fmt(ov["recall_decided_only"]))
    macro("SeedBestTP", ba["TP"])
    macro("SeedBestFN", ba["FN"])
    macro("SeedBestAbstPos", ba["abstain_pos"])
    macro("SeedBestFP", ba["FP"])
    macro("SeedBestTN", ba["TN"])
    macro("SeedBestAbstNeg", ba["abstain_neg"])
    macro("SeedBestPrec", fmt(ba["precision"]))
    macro("SeedBestRecA", fmt(ba["recall_abstain_as_miss"]))
    macro("SeedBestRecD", fmt(ba["recall_decided_only"]))
    macro("SeedBestAbst", fmt(ba["abstention_rate"]))
    macro("SeedContractCases", len(s["contract_cases"]))
    for k, n in (("static_only", "Static"), ("dynamic_only", "Dynamic"), ("combined", "Combined")):
        c = e[k]
        macro(f"Eng{n}TP", c["TP"])
        macro(f"Eng{n}Abst", c["abstain_pos"] + c["abstain_neg"])
        macro(f"Eng{n}TN", c["TN"])
        macro(f"Eng{n}FP", c["FP"])
        macro(f"Eng{n}FN", c["FN"])
    macro("SeedBlocksOnNegatives", len(s["blocks_on_negatives"]))
    inten = s["intentional_changes"]
    macro("SeedIntentBlocked", ", ".join(x["case_id"] for x in inten if x["blocked"]) or "none")
    macro("SeedIntentNotBlocked", ", ".join(x["case_id"] for x in inten if not x["blocked"]) or "none")
    inc = s["incremental_vs_full"]
    macro("SeedIncCompared", inc["obligations_compared"])
    macro("SeedIncMismatch", inc["mismatched"])
    macro("SeedIncVerdictMismatch", ", ".join(inc["verdict_mismatch_quick_vs_full"]) or "none")
    macro("SeedIncVerdictMismatchN", len(inc["verdict_mismatch_quick_vs_full"]))
    macro("SeedWarmCompared", inc["warm_obligations_compared"])
    macro("SeedWarmMismatch", inc["warm_mismatched"])
    sa = s["solver_ablation"]
    macro("SeedSolverCompared", sa["obligations_compared"])
    macro("SeedSolverChanged", sa["mismatched"])
    macro("SeedSolverVerdictChanged", len(sa["verdicts_changed"]))
    # anticipated vs actual (author's a-priori expectation)
    fn_cases = [r["case_id"] for r in d["cases"] if r["ground_truth"]["label"] == "defect_introduced"
                and r["prediction"].get("execute", r["prediction"]["quick"]) == "clear"]
    macro("SeedFalseNegCases", ", ".join(fn_cases) or "none")
    abst = [r["case_id"] for r in d["cases"] if r["ground_truth"]["label"] == "defect_introduced"
            and r["prediction"].get("execute", r["prediction"]["quick"]) == "abstain"]
    macro("SeedAbstPosCases", ", ".join(abst) or "none")
    absn = [r["case_id"] for r in d["cases"] if r["ground_truth"]["label"] == "no_defect_introduced"
            and r["prediction"].get("execute", r["prediction"]["quick"]) == "abstain"]
    macro("SeedAbstNegCases", ", ".join(absn) or "none")
    dyn_ms = [r["runs"]["execute"]["timing_ms"].get("dynamic") for r in d["cases"] if "execute" in r["runs"] and r["runs"]["execute"].get("timing_ms")]
    dyn_ms = [x for x in dyn_ms if isinstance(x, (int, float))]
    if dyn_ms:
        macro("SeedDynamicMedianMs", ms(statistics.median(dyn_ms)))
    def med_wall(run: str) -> str:
        xs = [r["runs"][run]["wall_ms"] for r in d["cases"] if run in r["runs"] and "wall_ms" in r["runs"][run]]
        return ms(statistics.median(xs)) if xs else "--"
    macro("SeedQuickWallMedian", med_wall("quick"))
    macro("SeedFullWallMedian", med_wall("full"))
    macro("SeedExecWallMedian", med_wall("execute"))
    probe = [r["runs"]["execute"]["timing_ms"].get("runner_probe") for r in d["cases"] if "execute" in r["runs"]]
    probe = [x for x in probe if isinstance(x, (int, float))]
    if probe:
        macro("SeedRunnerProbeMedianMs", ms(statistics.median(probe)))
    macro("SeedExecCases", sum(1 for r in d["cases"] if "execute" in r["runs"]))
    inc_neg = [r["case_id"] for r in d["cases"] if r["ground_truth"]["label"] == "no_defect_introduced"
               and r["runs"].get("execute", r["runs"]["quick"]).get("verdict") == "INCOMPLETE"]
    macro("SeedNegIncomplete", len(inc_neg))
    macro("SeedNegIncompleteCases", ", ".join(inc_neg))
    macro("SeedWarnFlags", ", ".join(r["case_id"] for r in d["cases"] if r["ground_truth"]["label"] == "defect_introduced"
                                     and r["runs"]["quick"].get("verdict") == "WARN") or "none")
    macro("SeedLabelsFrozen", d["manifest"]["labels_frozen_at"])
    macro("SeedManifestDigest", d["manifest"]["sha256"].split(":")[1][:12])


# ----------------------------------------------------------------------------------- latency
def outcome_text(c: dict[str, Any]) -> str:
    """Verdict/run outcome of a latency condition; with mixed outcomes, the number of runs of each."""
    outs = [f"{r['verdict']}/{r['run_outcome']}" for r in c["runs"]]
    cnt = Counter(outs)
    short = lambda o: o.replace("INCOMPLETE/INCOMPLETE", "INCOMPLETE").replace("/COMPLETE", "")  # noqa: E731
    if len(cnt) == 1:
        return short(outs[0])
    return "; ".join(f"{short(o)} ({n})" for o, n in sorted(cnt.items()))



# DECISIONS I-111 (pre-registered): the repeat at the end of the pipeline is the primary latency run; the first run
# (taken under heavy host load) is reported alongside in tab_latency_runs.tex.
PRIMARY_LATENCY = "paper_latency_repeat"
FIRST_LATENCY = "paper_latency"


def _load_med(c: dict[str, Any]) -> float | None:
    xs = [r["load_avg"][0] for r in c["runs"] if r.get("load_avg")]
    return statistics.median(xs) if xs else None


def latency_runs() -> None:
    a, b = load(FIRST_LATENCY), load(PRIMARY_LATENCY)
    key = lambda c: (c["functions"], c["change"], c["mode"], c["cache"])  # noqa: E731
    bmap = {key(c): c for c in b["conditions"]}
    rows = []
    for c in a["conditions"]:
        if c["change"] == "callee" or key(c) not in bmap:
            continue
        r = bmap[key(c)]
        pa, pb = c["summary"]["wall_ms"]["p50"], r["summary"]["wall_ms"]["p50"]
        rows.append([f"{c['functions']:,}".replace(",", "{,}"), c["change"], c["mode"], c["cache"], c["summary"]["n"],
                     ms(pa), f"{_load_med(c):.0f}", tex(outcome_text(c).replace("INCOMPLETE", "INC")), ms(pb), f"{_load_med(r):.0f}",
                     tex(outcome_text(r).replace("INCOMPLETE", "INC")), f"{pa / pb:.2f}"])
    write("tab_latency_runs.tex", table("rlllrrrlrrlr", ["fns", "change", "mode", "cache", "n", "p50 first", "load", "verdict first", "p50 repeat",
                                                         "load", "verdict repeat", "ratio"], rows,
          "The two latency runs (DECISIONS I-111): the first run (pipeline order, with host load spikes) and the pre-registered repeat at "
          "the end of the pipeline, which the main text reports. Wall p50 in ms; \\emph{load}: median 1-minute load average after each run "
          "(10 logical CPUs); \\emph{ratio}: first / repeat. INC = INCOMPLETE: required obligations stayed UNKNOWN (in quick mode the 2\\,s budget ran out; in full mode "
          "\\LatFullUnresolvedMin--\\LatFullUnresolvedMax{} obligations stayed UNKNOWN without the 600\\,s budget being reached).", "tab:latency-runs", star=True, placement="h!",
          size=r"\footnotesize", colsep="4pt"))
    la = [r["load_avg"][0] for c in a["conditions"] for r in c["runs"] if r.get("load_avg")]
    macro("LatFirstLoadMedian", f"{statistics.median(la):.1f}")
    macro("LatFirstLoadMax", f"{max(la):.1f}")
    full_inc = sum(1 for c in a["conditions"] if c["mode"] == "full" for r in c["runs"] if r["run_outcome"] != "COMPLETE")
    full_n = sum(len(c["runs"]) for c in a["conditions"] if c["mode"] == "full")
    macro("LatFirstFullIncomplete", full_inc)
    macro("LatFirstFullRuns", full_n)
    full_inc_b = sum(1 for c in b["conditions"] if c["mode"] == "full" for r in c["runs"] if r["run_outcome"] != "COMPLETE")
    macro("LatRepeatFullIncomplete", full_inc_b)
    macro("LatRepeatFullRuns", sum(len(c["runs"]) for c in b["conditions"] if c["mode"] == "full"))
    unres = [r["required"] - r["resolved"] for d_ in (a, b) for c in d_["conditions"] if c["mode"] == "full" for r in c["runs"] if r["run_outcome"] != "COMPLETE"]
    if unres:
        macro("LatFullUnresolvedMin", min(unres))
        macro("LatFullUnresolvedMax", max(unres))
    ratios = [float(r[-1]) for r in rows]
    macro("LatRatioMin", f"{min(ratios):.2f}")
    macro("LatRatioMax", f"{max(ratios):.2f}")
    macro("LatRatioMedian", f"{statistics.median(ratios):.2f}")
    # swap in use during each latency part (host_load.log samples inside the part's start/finish window from paper_manifest.json)
    man = load("paper_manifest")["parts"]
    samples = [line.split() for line in (RES / "logs" / "host_load.log").read_text().splitlines() if "swap_used_mb=" in line]
    for part, nm in (("latency", "First"), ("latency_repeat", "Repeat")):
        w = man.get(part) or {}
        sw = [float(x[2].split("=")[1]) / 1024 for x in samples if w and w["started_at"] <= x[0] <= w["finished_at"]]
        if sw:
            macro(f"LatSwap{nm}Min", f"{min(sw):.1f}")
            macro(f"LatSwap{nm}Max", f"{max(sw):.1f}")
            macro(f"LatSwap{nm}Samples", len(sw))
    tot = {k: sum(c["quick_cold_vs_full_cold"][k] for c in a["consistency"]) for k in ("compared", "mismatched", "coverage_loss", "missed_introduced", "introduced_full")}
    macro("ConsFirstCompared", f"{tot['compared']:,}".replace(",", "{,}"))
    macro("ConsFirstMismatch", tot["mismatched"])


def latency() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    d = load(PRIMARY_LATENCY)
    conds = d["conditions"]
    rows = []
    for c in conds:
        if c["change"] == "callee":
            continue
        s = c["summary"]
        rows.append([f"{c['functions']:,}".replace(",", "{,}"), c["change"], c["mode"], c["cache"], s["n"], ms(s["wall_ms"]["p50"]), ms(s["wall_ms"]["p95"]),
                     ms(s["engine_ms"]["p50"]), f"{s['peak_rss_mb']['p50']:.0f}", ",".join(f"{x:,}".replace(",", "{,}") for x in s["affected_symbols"]),
                     tex(outcome_text(c)),
                     f"{statistics.median([r['load_avg'][0] for r in c['runs'] if r.get('load_avg')]):.0f}"])
    write("tab_latency.tex", table("rllllrrrrrlr", ["fns", "change", "mode", "cache", "n", "wall p50", "wall p95", "engine p50", "RSS MB",
                                                     "affected", "verdict (runs)", "load"], rows,
          "Static \\texttt{guard verify} latency on synthetic repositories (ms; fresh child process per run, interpreter start included). "
          "\\emph{leaf}: one function body changes; \\emph{hub}: a constant read by most functions changes. Default 2\\,s quick budget. "
          "\\emph{load}: median 1-minute load average sampled after each run (10 logical CPUs; values far above 10 mean heavy contention "
          "from other processes on the host).",
          "tab:latency", star=True, placement="h!", size=r"\footnotesize", colsep="4pt"))
    rows = []
    for c in d["consistency"]:
        q = c["quick_cold_vs_full_cold"]
        w = c.get("quick_warm_vs_full_cold")
        n = lambda x: f"{x:,}".replace(",", "{,}")  # noqa: E731
        rows.append([n(c["functions"]), c["change"], n(q["compared"]), q["mismatched"], n(q["coverage_loss"]), n(q["introduced_full"]),
                     n(q["introduced_quick"]), n(q["missed_introduced"]), tex(f"{q['quick_verdict']}/{q['full_verdict']}"),
                     "--" if not w else f"{n(w['compared'])}/{w['mismatched']}/{n(w['coverage_loss'])}"])
    write("tab_consistency.tex", table("rlrrrrrrll", ["fns", "change", "compared", "mismatch", "cov. loss", r"intro.\textsubscript{full}",
                                                      r"intro.\textsubscript{quick}", "missed", "verdict q/f", "warm c/m/l"], rows,
          "Incremental (quick, affected slice) versus full analysis at equal configuration: obligations of the quick report compared by ID "
          "with the full report. \\emph{mismatch}: both make a claim and the claims differ; \\emph{cov. loss}: quick is UNKNOWN (budget) "
          "where full decides; \\emph{missed}: INTRODUCED findings of the full run absent from the quick run. "
          "\\emph{callee}: a shared helper change that introduces real defects in unchanged callers.", "tab:consistency", star=True))
    # per-phase breakdown (docs/12: separate capture, analysis, solver and impact time), p50 over the runs of a condition
    phases = (("capture", "capture"), ("base_capture", "base capture"), ("static", "analysis"), ("base_static", "base analysis"),
              ("solver", "solver"), ("impact", "impact"))
    rows = []
    for c in conds:
        if c["change"] == "callee" or c["cache"] != "cold":
            continue
        med = {k: statistics.median([(r["timing_ms"].get(k) or 0.0) for r in c["runs"]]) for k, _ in phases}
        rows.append([f"{c['functions']:,}".replace(",", "{,}"), c["change"], c["mode"]] + [ms(med[k]) for k, _ in phases]
                    + [ms(c["summary"]["engine_ms"]["p50"]), ms(c["summary"]["wall_ms"]["p50"])])
    write("tab_phases.tex", table("rllrrrrrrrr", ["fns", "change", "mode"] + [h for _, h in phases] + ["engine", "wall"], rows,
          "Where cold \\texttt{guard verify} time goes (p50 per phase, ms). \\emph{capture}: reading and hashing the candidate snapshot "
          "(working tree); \\emph{base capture}: the base commit (Git object reads); \\emph{analysis}: lowering and static analysis of the "
          "candidate (solver time included); \\emph{solver}: Z3 time alone; \\emph{impact}: matching symbols and computing the slice. "
          "\\emph{engine} is the sum of the report's timing fields excluding solver; \\emph{wall} adds interpreter start, imports and report "
          "serialization/validation. No model is involved in any of these runs.", "tab:phases", star=True, placement="h!"))

    # macros
    def get(fn_scale: int, change: str, mode: str, cache: str) -> dict[str, Any] | None:
        for c in conds:
            if abs(c["functions"] - fn_scale) <= 5 and c["change"] == change and c["mode"] == mode and c["cache"] == cache:
                return c["summary"] | {"_cond": c}
        return None
    for scale, nm in ((100, "H"), (1000, "K"), (10000, "TK")):
        for change, cn in (("leaf", "Leaf"), ("hub", "Hub")):
            for mode, mn in (("quick", "Quick"), ("full", "Full")):
                for cache, kn in (("cold", "Cold"), ("warm", "Warm")):
                    s = get(scale, change, mode, cache)
                    if s:
                        macro(f"Lat{nm}{cn}{mn}{kn}Pfifty", ms(s["wall_ms"]["p50"]))
                        macro(f"Lat{nm}{cn}{mn}{kn}Pninetyfive", ms(s["wall_ms"]["p95"]))
                        macro(f"Lat{nm}{cn}{mn}{kn}N", s["n"])
                        macro(f"Lat{nm}{cn}{mn}{kn}Rss", f"{s['peak_rss_mb']['p50']:.0f}")
                        macro(f"Lat{nm}{cn}{mn}{kn}Outcome", outcome_text(s["_cond"]))
    macro("LatMaxRss", f"{max(c['summary']['peak_rss_mb']['max'] for c in conds):,.0f}".replace(",", "{,}"))
    macro("LatReps", d["settings"]["reps"])
    macro("LatRepsLarge", d["settings"]["reps_large"])
    tot = {k: sum(c["quick_cold_vs_full_cold"][k] for c in d["consistency"]) for k in ("compared", "mismatched", "coverage_loss", "missed_introduced", "introduced_full")}
    macro("ConsCompared", f"{tot['compared']:,}".replace(",", "{,}"))
    macro("ConsMismatch", tot["mismatched"])
    macro("ConsCovLoss", f"{tot['coverage_loss']:,}".replace(",", "{,}"))
    macro("ConsMissed", f"{tot['missed_introduced']:,}".replace(",", "{,}"))
    macro("ConsIntroFull", f"{tot['introduced_full']:,}".replace(",", "{,}"))
    loads = [x for c in conds for r in c["runs"] for x in [r["load_avg"][0]] if r.get("load_avg")]
    if loads:
        macro("LatLoadMin", f"{min(loads):.1f}")
        macro("LatLoadMax", f"{max(loads):.1f}")
        macro("LatLoadMedian", f"{statistics.median(loads):.1f}")
    # figure
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.6), sharey=True)
    styles = {("quick", "cold"): ("o", "-", "#1f5aa6"), ("quick", "warm"): ("o", "--", "#6fa0dc"), ("full", "cold"): ("s", "-", "#b5442c"),
              ("full", "warm"): ("s", "--", "#e39a87")}
    for ax, change in zip(axes, ("leaf", "hub")):
        for (mode, cache), (mk, ls, col) in styles.items():
            pts = sorted((c["functions"], c["summary"]["wall_ms"]["p50"], c["summary"]["wall_ms"]["p95"]) for c in conds
                         if c["change"] == change and c["mode"] == mode and c["cache"] == cache)
            if not pts:
                continue
            xs = [p[0] for p in pts]
            ax.plot(xs, [p[1] / 1000 for p in pts], marker=mk, linestyle=ls, color=col, label=f"{mode} {cache}", linewidth=1.2, markersize=4)
            ax.fill_between(xs, [p[1] / 1000 for p in pts], [p[2] / 1000 for p in pts], color=col, alpha=0.12, linewidth=0)
        ax.axhline(2.0, color="#555555", linewidth=0.7, linestyle=":")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_title(f"{change} change", fontsize=9)
        ax.set_xlabel("supported functions", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(True, which="major", linewidth=0.3, alpha=0.5)
    axes[0].set_ylabel("wall time (s), p50; band to p95", fontsize=8)
    axes[1].legend(fontsize=7, frameon=False, loc="upper left")
    fig.tight_layout()
    fig.savefig(OUT / "fig_latency.pdf")
    plt.close(fig)


# ----------------------------------------------------------------------------------- proposers (templates, local model, agent)
AGENT_RUNS = ("run1", "run2", "run3")


def _mean_sd(xs: list[float]) -> str:
    xs = [x for x in xs if x is not None]
    if not xs:
        return "--"
    m = statistics.mean(xs)
    sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
    nd = 2 if all(isinstance(x, float) and x <= 1.0 for x in xs) else 1
    return f"{m:.{nd}f} ({sd:.{nd}f})"


def model() -> None:
    d = load("paper_model_eval")
    t = d["arms"]["templates"]["repeats"]
    m = d["arms"]["local_model"]["repeats"]
    agent_docs = [load(f"paper_agent_eval_{r}") for r in AGENT_RUNS]
    a = [x["agent_proposed"] for x in agent_docs]
    nseeded = d["heldout"]["seeded_bugs"]

    def cell(reps: list[dict[str, Any]], k: str | None, f=None) -> str:
        xs = [f(r) if f else r.get(k) for r in reps]
        if not xs:
            return "--"
        if all(isinstance(x, float) or x is None for x in xs) and any(isinstance(x, float) for x in xs):
            vals = ", ".join(fmt(x) for x in xs)
        else:
            vals = ", ".join("--" if x is None else str(x) for x in xs)
        return vals

    exposed = lambda r: len(r["seeded_bug_functions_exposed"])  # noqa: E731
    wall = lambda r: round(r["wall_ms"] / 1000)  # noqa: E731
    spec = [("raw candidates", "raw_candidates", None),
            ("schema-valid rate", "schema_valid_rate", None),
            ("non-vacuous rate", "nonvacuous_rate", None),
            ("mech.\\ checkable rate", "mechanically_checkable_rate", None),
            ("distinct validated", "distinct_validated", None),
            ("\\quad new beyond templates", "new_beyond_templates", None),
            ("TESTED (distinct)", "tested", None),
            ("REFUTED (distinct)", "refuted", None),
            ("\\quad false claim", "false_claims_refuted", None),
            ("\\quad valid for intent", "refuted_claim_valid_for_intended_behavior", None),
            (f"seeded bugs exposed (of {nseeded})", None, exposed),
            ("wall time (s)", None, wall)]
    rows = []
    for label, k, f in spec:
        tcell = "--" if k == "new_beyond_templates" else cell(t, k, f)
        rows.append([label, tcell, cell(m, k, f), cell(a, k, f)])
    write("tab_model.tex", table("llll", ["metric (one value per repeat)", "templates", "local model", "agent-proposed"], rows,
          f"Contract proposers on the held-out set ({d['heldout']['functions']} functions, {nseeded} seeded bugs), all through the same "
          "validation, non-vacuity filter, de-duplication and Docker falsification (32 probes). \\emph{Templates}: deterministic, 3 repeats. "
          "\\emph{Local model}: qwen2.5-coder:7b, temperature 0, at most 3 candidates per function, 3 repeats. \\emph{Agent-proposed}: "
          "Claude Sonnet 5.5 subagents that saw only the context packets and the protocol README, at most 8 candidates per function, 3 "
          "independent runs; templates ran in the same pass so duplicates of template claims are excluded from \\emph{new beyond templates}. "
          "Rates use each source's raw candidates as denominator. Wall time includes model inference (local model) or only validation "
          "and falsification (agent: proposals were written beforehand; each run took under two minutes by its session timestamps).",
          "tab:model", star=True))
    # local model macros
    per_fn = [f["elapsed_ms"] for r in m for f in r["per_function"] if f.get("status") == "ANSWERED" and f.get("elapsed_ms")]
    macro("ModelRepeats", len(m))
    macro("ModelFunctions", d["heldout"]["functions"])
    macro("ModelSeeded", nseeded)
    macro("ModelCalls", len(per_fn))
    macro("ModelAttempted", sum(r.get("functions_attempted") or 0 for r in m))
    if per_fn:
        srt = sorted(per_fn)
        p95 = srt[max(0, min(len(srt) - 1, int(round(0.95 * len(srt) + 0.5)) - 1))]
        macro("ModelLatPfifty", f"{statistics.median(per_fn) / 1000:.1f}")
        macro("ModelLatPninetyfive", f"{p95 / 1000:.1f}")
        macro("ModelLatMin", f"{min(per_fn) / 1000:.1f}")
        macro("ModelLatMax", f"{max(per_fn) / 1000:.1f}")
    macro("ModelExposed", ", ".join(str(exposed(r)) for r in m))
    macro("ModelExposedFns", ", ".join(sorted({f.split("::")[1] for r in m for f in r["seeded_bug_functions_exposed"]})))
    macro("TemplateExposed", ", ".join(str(exposed(r)) for r in t))
    macro("ModelFalseClaims", ", ".join(str(r["false_claims_refuted"]) for r in m))
    macro("ModelRawCands", ", ".join(str(r["raw_candidates"]) for r in m))
    macro("ModelNewBeyond", ", ".join(str(r["new_beyond_templates"]) for r in m))
    macro("ModelNonvacRate", _mean_sd([r["nonvacuous_rate"] for r in m]))
    macro("TemplateRawCands", ", ".join(str(r["raw_candidates"]) for r in t))
    macro("TemplateTested", ", ".join(str(r["tested"]) for r in t))
    macro("TemplateRefuted", ", ".join(str(r["refuted"]) for r in t))
    ident = d["summary"]["local_model"].get("identical_responses_across_repeats")
    macro("ModelIdentical", "yes" if ident else ("no" if ident is False else "n/a"))
    distinct_hashes = len({tuple(r.get("response_sha256") or []) for r in m})
    macro("ModelDistinctResponseSets", distinct_hashes)
    rss = [r.get("inference_server_peak_rss_mb") for r in m if r.get("inference_server_peak_rss_mb")]
    if rss:
        macro("ModelRss", f"{max(rss):,.0f}".replace(",", "{,}"))
    tps = [x for r in m for x in (r.get("output_tokens_per_s") or {}).get("values", [])]
    if tps:
        macro("ModelTokPerS", f"{statistics.median(tps):.1f}")
    macro("ModelWallMin", f"{statistics.mean([r['wall_ms'] for r in m]) / 60000:.0f}")
    # agent macros
    macro("AgentRuns", len(a))
    macro("AgentRawCands", ", ".join(str(r["raw_candidates"]) for r in a))
    macro("AgentExposed", ", ".join(str(exposed(r)) for r in a))
    macro("AgentExposedFns", ", ".join(sorted({f.split("::")[1] for r in a for f in r["seeded_bug_functions_exposed"]})))
    macro("AgentFalseClaims", ", ".join(str(r["false_claims_refuted"]) for r in a))
    macro("AgentNewBeyond", ", ".join(str(r["new_beyond_templates"]) for r in a))
    macro("AgentNonvacRate", _mean_sd([r["nonvacuous_rate"] for r in a]))
    macro("AgentSchemaRate", _mean_sd([r["schema_valid_rate"] for r in a]))
    macro("AgentRejectedWhole", sum(r["responses_rejected_whole"] for r in a))
    macro("AgentRunOutcomes", ", ".join(sorted({x["run_outcome"] for x in agent_docs})))
    macro("AgentNotProposed", sum(len(x["proposals"]["symbols_not_proposed"]) for x in agent_docs))
    codes: Counter = Counter()
    for r in a:
        codes.update(r["rejection_codes"])
    macro("AgentTopRejections", ", ".join(f"{k} ({v})" for k, v in codes.most_common(3)) or "none")
    mcodes: Counter = Counter()
    for r in m:
        mcodes.update(r["rejection_codes"])
    macro("ModelTopRejections", ", ".join(f"{k} ({v})" for k, v in mcodes.most_common(3)) or "none")
    macro("AgentFalsificationMin", f"{statistics.mean([r['wall_ms'] for r in a]) / 60000:.1f}")
    # per-function exposure overlap between arms
    ex_m = {f for r in m for f in r["seeded_bug_functions_exposed"]}
    ex_a = {f for r in a for f in r["seeded_bug_functions_exposed"]}
    macro("ExposedUnionBoth", len(ex_m | ex_a))
    macro("ExposedOnlyAgent", ", ".join(sorted(f.split("::")[1] for f in ex_a - ex_m)) or "none")
    macro("ExposedOnlyModel", ", ".join(sorted(f.split("::")[1] for f in ex_m - ex_a)) or "none")
    macro("ExposedEveryAgentRun", ", ".join(sorted(f.split("::")[1] for f in set.intersection(*[set(r["seeded_bug_functions_exposed"]) for r in a]))) or "none")
    macro("ExposedEveryModelRun", ", ".join(sorted(f.split("::")[1] for f in set.intersection(*[set(r["seeded_bug_functions_exposed"]) for r in m]))) or "none")
    man = json.loads((REPO / "benchmarks" / "manifests" / "m4_heldout.json").read_text())
    macro("SeededDslExpressible", sum(1 for f in man["functions"] if f["seeded_bug"] and f["seeded_bug"].get("dsl_expressible")))


def transport() -> None:
    d = load("paper_transport_openai_compat")
    r = d["arms"]["openai_compatible_loopback"]["repeats"][0]
    answered = [f for f in r["per_function"] if f.get("status") == "ANSWERED"]
    macro("TransportStatus", r["model_status"])
    macro("TransportFunctions", r["functions_attempted"])
    macro("TransportAnswered", r["functions_answered"])
    macro("TransportRaw", r["raw_candidates"])
    macro("TransportSchemaRate", fmt(r["schema_valid_rate"]))
    macro("TransportNonvac", r["nonvacuous"])
    so = Counter((o or {}).get("structured_output") for o in r.get("request_options") or [] if o)
    macro("TransportStructured", ", ".join(f"{k} ({v})" for k, v in so.items()) or "--")
    served = sorted({(o or {}).get("served_model") for o in r.get("request_options") or [] if o and (o or {}).get("served_model")})
    macro("TransportServed", ", ".join(served) or "--")
    eg = r.get("data_egress") or {}
    macro("TransportLeft", "no" if eg.get("context_left_machine") is False else ("yes" if eg.get("context_left_machine") else "--"))
    lat = [f["elapsed_ms"] for f in answered if f.get("elapsed_ms")]
    if lat:
        macro("TransportLatPfifty", f"{statistics.median(lat) / 1000:.1f}")
    macro("TransportExposed", len(r["seeded_bug_functions_exposed"]))
    macro("TransportWallMin", f"{r['wall_ms'] / 60000:.0f}")


def host() -> None:
    d = load("paper_manifest")
    h = d["last_invocation"]["host"]
    macro("HostCpu", h.get("cpu"))
    macro("HostModel", h.get("model"))
    macro("HostRamGB", f"{int(h.get('memory_bytes') or 0) / 2**30:.0f}")
    macro("HostOs", h.get("os"))
    macro("HostPython", h.get("python"))
    macro("HostZThree", h.get("z3"))
    macro("HostEngine", h.get("engine_version"))
    macro("HostSemantics", h.get("semantics_profile"))
    macro("HostDocker", h.get("docker_server"))
    macro("HostOllama", (h.get("ollama") or "").replace("ollama version is ", ""))
    macro("HostSourceDigest", (h.get("source_tree_sha256") or ":")[7:19])
    parts = d["parts"]
    macro("RunDates", ", ".join(sorted({p["started_at"][:10] for p in parts.values()})))


def host_load() -> None:
    """benchmarks/results/logs/host_load.log: one sample per minute (1-min load average, swap used, memory free %, running
    containers) taken while run_all.py was running (sampling started during the latency part, see paper/README.md)."""
    import re

    path = RES / "logs" / "host_load.log"
    rows = [dict(kv.split("=") for kv in line.split()[1:]) | {"t": line.split()[0]} for line in path.read_text().splitlines() if "load=" in line]
    if not rows:
        return
    loads = [float(r["load"]) for r in rows]
    swap = [float(r["swap_used_mb"]) for r in rows if re.fullmatch(r"[0-9.]+", r["swap_used_mb"])]
    macro("HostLoadSamples", len(rows))
    macro("HostLoadMedian", f"{statistics.median(loads):.0f}")
    macro("HostLoadMax", f"{max(loads):.0f}")
    macro("HostSwapMaxGB", f"{max(swap) / 1024:.1f}" if swap else "--")
    macro("HostSwapMedianGB", f"{statistics.median(swap) / 1024:.1f}" if swap else "--")
    macro("HostContainers", max(int(r["containers"]) for r in rows))
    macro("HostLoadWindow", f"{rows[0]['t'][:10]} {rows[0]['t'][11:16]} to {rows[-1]['t'][:10]} {rows[-1]['t'][11:16]}")


def implementation() -> None:
    src = list((REPO / "src" / "invariantguard").rglob("*.py"))
    tests = list((REPO / "tests").rglob("*.py"))
    macro("ImplSrcLines", f"{sum(len(p.read_text(encoding='utf-8').splitlines()) for p in src):,}".replace(",", "{,}"))
    macro("ImplSrcFiles", len(src))
    macro("ImplTestLines", f"{sum(len(p.read_text(encoding='utf-8').splitlines()) for p in tests):,}".replace(",", "{,}"))
    t = load("paper_test_suite")
    macro("TestSummary", t["summary_line"].split(" in ")[0])
    macro("TestSeconds", f"{t['elapsed_s']:.0f}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    import sys

    missing = []
    for f in (host, host_load, implementation, semantic, seeded, model, transport, latency, latency_runs):
        try:
            f()
        except FileNotFoundError as exc:  # a part has not been run: say so; the paper will not compile without it
            missing.append(f"{f.__name__}: {exc.filename}")
    for m in missing:
        print(f"MISSING results for {m}", file=sys.stderr)
    lines = ["% Generated by paper/make_tables.py from benchmarks/results/paper_*.json. Do not edit."]
    for k, v in sorted(MACROS.items()):
        lines.append(rf"\newcommand{{\{k}}}{{{v}}}")
    write("numbers.tex", "\n".join(lines) + "\n")
    print(f"wrote {len(MACROS)} macros and tables to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
