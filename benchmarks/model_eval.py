"""M4 T44: templates-only vs. local-model contract candidates on a held-out set.

Usage (real model and real Docker runner; nothing is mocked):

    .venv/bin/python benchmarks/model_eval.py --repeats 3 --out benchmarks/results/m4_model_eval.json

Arms
* no_model_no_templates: no discovery at all (0 candidates; the trivial baseline).
* templates: deterministic templates (``guard contracts discover --execute``).
* local_model: the configured local model through the same pipeline (``--model local --execute``).

Every run is a dry run in a fresh temporary copy of benchmarks/fixtures/m4_heldout, so runs
are independent and nothing is recorded. Every candidate (from either source) passes the
same validation, non-vacuity filter, de-duplication and dynamic falsification in the
capability-checked Docker runner.

Metrics, per arm and repeat (denominator: raw candidates proposed by that source):
* schema_valid: passed the strict response/candidate schema, DSL typing and symbol checks;
* nonvacuous: additionally has an admissible precondition and a behavior-falsifiable predicate;
* mechanically_checkable: validated and falsification produced a decidable result (TESTED or REFUTED);
* refuted / tested / unknown: falsification outcomes on the current (held-out) code;
* claim validity: a REFUTED claim is evaluated (DSL evaluator) on the intended behavior (the reference
  in benchmarks/m4_reference.py for seeded-bug functions, the fixture itself otherwise) at its
  counterexample and at 32 admitted inputs sampled with a fixed seed; it is "valid for the intended
  behavior" only if all of them pass (a finite check, not a proof);
* seeded bugs exposed: a REFUTED claim that is valid for the intended behavior and fails for the
  fixture at its minimized counterexample;
* false claims: REFUTED claims not valid for the intended behavior; refutations caused by a DSL type
  mismatch (e.g. a list operator applied to a scalar result) are counted separately;
* duplicates of an earlier claim (a template or an earlier candidate) take that claim's falsification
  outcome for the checkable rate and are excluded from refuted/tested counts;
* new beyond templates: validated model candidates that are not duplicates of a template candidate;
* time per candidate (model wall time / raw candidates), tokens, and peak RSS of the inference
  server process (sampled every 0.5 s) and of this process.

Ground-truth oracle: the seeded-bug check executes fixture and reference code (both written for
this benchmark, trusted) in an isolated child interpreter (``python -I``) on the host.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "benchmarks" / "fixtures" / "m4_heldout"
MANIFEST = REPO / "benchmarks" / "manifests" / "m4_heldout.json"
REFERENCE = REPO / "benchmarks" / "m4_reference.py"

STRUCTURAL = {"MALFORMED_JSON", "MALFORMED_RESPONSE", "MALFORMED_CANDIDATE", "FORBIDDEN_FIELD", "UNTYPED_TEXT", "PROFILE_MISMATCH", "INVALID_SOURCE_REF",
              "TOO_MANY_CANDIDATES", "UNKNOWN_NODE", "MALFORMED_NODE", "TYPE_ERROR", "UNKNOWN_SYMBOL", "BUDGET_EXCEEDED", "UNSUPPORTED_NODE",
              "MALFORMED_CONTRACT", "SCHEMA", "MISSING_DOMAIN", "MISSING_PROVENANCE", "OVERSIZED", "UNKNOWN_PROFILE", "UNSUPPORTED_TARGET"}
VALIDITY_SAMPLE = 32
VACUITY = {"VACUOUS", "VACUOUS_PREDICATE", "INVALID_CONTRACT", "CONTRADICTORY_DOMAIN"}


def sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


class RssSampler:
    """Peak RSS of the inference server (llama-server / ollama runner) while a run is active."""

    def __init__(self) -> None:
        self.peak_kb = 0
        self.samples = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            pids = sh(["pgrep", "-f", "llama-server|ollama runner"]).split()
            for pid in pids:
                v = sh(["ps", "-o", "rss=", "-p", pid])
                if v.isdigit():
                    self.peak_kb = max(self.peak_kb, int(v))
            self.samples += 1
            self._stop.wait(0.5)

    def __enter__(self) -> "RssSampler":
        self._t.start()
        return self

    def __exit__(self, *a: Any) -> None:
        self._stop.set()
        self._t.join(5)


def make_workspace(tmp: Path, args: argparse.Namespace, seed: int) -> Path:
    root = tmp / f"ws-{time.time_ns()}"
    shutil.copytree(FIXTURE, root)
    (root / ".git").mkdir()  # stop config discovery at this root (no git repository is needed)
    (root / ".guard").mkdir()
    (root / ".guard" / "config.toml").write_text(f'''schema_version = "1.0"
[execution]
enabled = true
profile = "iso"
[execution.profiles.iso]
backend = "docker"
image = "{args.image}"
allowed_targets = ["heldout/*.py::*"]
max_probes = {args.max_probes}
{semantic_section(args, seed)}''', encoding="utf-8")
    return root


def semantic_section(args: argparse.Namespace, seed: int) -> str:
    """[semantic] for the native Ollama loopback adapter (the M4 arm) or, for the transport check, the
    OpenAI-compatible adapter pointed at the same local Ollama server's /v1 endpoint (loopback only)."""
    if args.provider == "loopback":
        return f'''[semantic]
enabled = true
endpoint = "{args.endpoint}"
model_id = "{args.model}"
model_digest = "{args.digest}"
temperature = {args.temperature}
seed = {seed}
request_timeout_s = {args.timeout}
'''
    return f'''[semantic]
enabled = true
provider = "openai_compatible"
base_url = "{args.base_url}"
model_id = "{args.model}"
temperature = {args.temperature}
seed = {seed}
request_timeout_s = {args.timeout}
'''


def run_discover(root: Path, model: bool) -> tuple[dict[str, Any], int, float]:
    from invariantguard.contracts import commands as cc

    ctx = cc.open_ctx(root, root / ".guard", root, root / ".guard" / "config.toml")
    t0 = time.perf_counter()
    try:
        out, code = cc.cmd_discover(ctx, cc.DiscoverOptions(dry_run=True, model="local" if model else None, execute=True))
    finally:
        ctx.close()
    return out, code, (time.perf_counter() - t0) * 1000


ORACLE = r"""
import copy, json, sys
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2]); sys.path.insert(0, sys.argv[3])
import importlib
from invariantguard.contracts.values import encode
ref = importlib.import_module("m4_reference")
cases = json.loads(sys.stdin.read())
out = []
for c in cases:
    mod = importlib.import_module(c["module"])
    impls = {"fixture": getattr(mod, c["function"])}
    if c.get("reference"):
        impls["reference"] = getattr(ref, c["reference"])
    res = {}
    for name, fn in impls.items():
        results, before, after = [], None, None
        for i in range(c["repeats"]):
            a = copy.deepcopy(c["args"])
            b = {k: encode(v) for k, v in a.items()}
            try:
                r = {"kind": "returned", "value": encode(fn(**a))}
            except Exception as e:
                r = {"kind": "raised", "exception": {"type": type(e).__name__, "message": str(e)[:200], "frames": []}}
            results.append(r)
            if i == 0:
                before, after = b, {k: encode(v) for k, v in a.items()}
        res[name] = {"id": "x", "status": "ok", "results": results, "before": before, "after": after}
    out.append(res)
print(json.dumps(out))
"""


def behaviors(fixture_root: Path, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Observations of the fixture (and the reference, if any) on each input, in harness format.
    Trusted benchmark code only, in an isolated child interpreter (python -I)."""
    if not cases:
        return []
    p = subprocess.run([sys.executable, "-I", "-c", ORACLE, str(fixture_root), str(REFERENCE.parent), str(REPO / "src")], input=json.dumps(cases),
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"oracle failed: {p.stderr[-2000:]}")
    return json.loads(p.stdout)


def _typed(c: dict[str, Any], files: dict[str, bytes]) -> Any:
    from invariantguard.contracts.model import validate_revision

    doc = {"schema_version": "1.0", "contract_id": "eval.x", "revision": 1, "scope": c["scope"], "authority": "INFERRED", "lifecycle_state": "VALIDATED",
           "precondition": c["precondition"], "predicate": c["predicate"], "input_profile": c["input_profile"],
           "provenance": [{"kind": "template", "reference": "eval"}], "supersedes": None, "reason": "eval", "created_at": "2026-01-01T00:00:00Z"}
    return validate_revision(doc, files, "sha256:" + "0" * 64)


def score(out: dict[str, Any], source: str, manifest: dict[str, Any], fixture_root: Path) -> dict[str, Any]:
    from invariantguard.contracts.checker import evaluate_observation

    seeded = {f["symbol"]: f["seeded_bug"] for f in manifest["functions"]}
    files = {p.relative_to(fixture_root).as_posix(): p.read_bytes() for p in fixture_root.rglob("*.py")}
    by_meaning = {c.get("meaning_digest"): c for c in out["candidates"] if c.get("meaning_digest") and c["status"] != "duplicate"}
    cands = [c for c in out["candidates"] if c["source"] == source]
    raw = len(cands)
    if source in ("local_model", "remote_model"):
        resp_rejected = sum(1 for f in out["model"].get("functions", []) if f.get("response_rejection"))
    elif source == "agent_proposed":  # benchmarks/agent_eval/score.py: whole batches rejected (stale, malformed, forbidden fields)
        resp_rejected = sum(1 for b in (out.get("agent") or {}).get("batches", []) if b.get("status") == "REJECTED")
    else:
        resp_rejected = 0
    codes = [c.get("rejection_code") for c in cands if c["status"] == "rejected"]
    structural = sum(1 for k in codes if k in STRUCTURAL)
    vacuity = sum(1 for k in codes if k in VACUITY)
    within_dup = sum(1 for k in codes if k == "DUPLICATE")
    other_rej = len(codes) - structural - vacuity - within_dup
    validated = [c for c in cands if c["status"] != "rejected"]

    def outcome(c: dict[str, Any]) -> dict[str, Any]:
        if c["status"] == "duplicate":  # same claim as a template/earlier candidate: its falsification applies
            orig = by_meaning.get(c.get("meaning_digest"))
            return (orig or {}).get("falsification") or {}
        return c.get("falsification") or {}

    outcomes = {id(c): outcome(c) for c in validated}
    distinct = [c for c in validated if c["status"] != "duplicate"]
    refuted = [c for c in distinct if outcomes[id(c)].get("status") == "REFUTED"]
    # Ground truth on each refuted claim: evaluate the claim on the intended behavior (reference for
    # seeded-bug functions, the fixture itself otherwise) at the minimized counterexample.
    cases = []
    for c in refuted:
        path, fn = c["scope"].split("::")
        bug = seeded.get(c["scope"])
        reps = c["predicate"]["repeats"] if isinstance(c["predicate"], dict) and c["predicate"].get("kind") == "repeatability" else 1
        cases.append({"module": path[:-3].replace("/", "."), "function": fn, "reference": bug["reference"] if bug else None,
                      "args": outcomes[id(c)]["counterexample"]["input"], "repeats": reps})
    from invariantguard.contracts.profiles import admit_inputs, domain_hints

    judged = []
    for c, case in zip(refuted, cases):
        tc = _typed(c, files)
        params = list(tc.target.params)
        sample = admit_inputs(tc.input_profile, params, tc.precondition_holds, domain_hints(tc.precondition, params), seed=20261008,
                              limit=VALIDITY_SAMPLE, examples=tc.examples)
        inputs = [case["args"]] + [a for a, _ in sample.inputs]
        obs = behaviors(fixture_root, [dict(case, args=a) for a in inputs])
        intended = [evaluate_observation(tc, a, o.get("reference") or o["fixture"], None)["verdict"] for a, o in zip(inputs, obs)]
        actual_ce = evaluate_observation(tc, inputs[0], obs[0]["fixture"], None)["verdict"]
        valid = all(v == "PASS" for v in intended)
        mismatch = any("type mismatch" in r for r in outcomes[id(c)].get("reasons", []))
        judged.append({"scope": c["scope"], "contract_id": c["contract_id"], "claim_valid_for_intended_behavior": valid,
                       "intended_checked_inputs": len(inputs), "intended_fail": intended.count("FAIL"), "intended_unknown": intended.count("UNKNOWN"),
                       "intended_verdict_at_counterexample": intended[0], "fixture_verdict_at_counterexample": actual_ce, "type_mismatch": mismatch,
                       "exposes_seeded_bug": bool(case["reference"]) and valid and actual_ce == "FAIL"})
    exposed_fns = sorted({j["scope"] for j in judged if j["exposes_seeded_bug"]})

    def rate(n: int) -> float | None:
        return round(n / raw, 4) if raw else None

    checkable = sum(1 for c in validated if outcomes[id(c)].get("status") in ("TESTED", "REFUTED"))
    return {
        "raw_candidates": raw, "responses_rejected_whole": resp_rejected,
        "rejected_structural": structural, "rejected_vacuity": vacuity, "rejected_duplicate_in_response": within_dup, "rejected_other": other_rej,
        "rejection_codes": {k: codes.count(k) for k in sorted(set(c for c in codes if c))},
        "schema_valid": raw - structural - other_rej, "schema_valid_rate": rate(raw - structural - other_rej),
        "nonvacuous": len(validated), "nonvacuous_rate": rate(len(validated)),
        "duplicate_of_earlier_claim": len(validated) - len(distinct),
        "distinct_validated": len(distinct),
        "new_beyond_templates": len(distinct) if source != "template" else None,
        "mechanically_checkable": checkable, "mechanically_checkable_rate": rate(checkable),
        "tested": sum(1 for c in distinct if outcomes[id(c)].get("status") == "TESTED"), "refuted": len(refuted),
        "unknown": sum(1 for c in distinct if outcomes[id(c)].get("status") in ("UNKNOWN", "UNSTABLE")),
        "refuted_by_type_mismatch": sum(1 for j in judged if j["type_mismatch"]),
        "refuted_claim_valid_for_intended_behavior": sum(1 for j in judged if j["claim_valid_for_intended_behavior"]),
        "false_claims_refuted": sum(1 for j in judged if not j["claim_valid_for_intended_behavior"]),
        "seeded_bug_functions_exposed": exposed_fns,
        "falsification_ms": round(sum((c.get("falsification") or {}).get("elapsed_ms") or 0 for c in distinct), 1),
        "refutation_judgements": judged,
        "candidates": [{"scope": c["scope"], "contract_id": c["contract_id"], "status": c["status"], "rejection_code": c.get("rejection_code"),
                        "reason": (c.get("reason") or "")[:240], "precondition": c.get("precondition"), "predicate": c.get("predicate"),
                        "input_profile": c.get("input_profile"), "meaning_digest": c.get("meaning_digest"),
                        "falsification": {k: outcomes.get(id(c), c.get("falsification") or {}).get(k) for k in ("status", "probes", "counterexample", "reasons")}}
                       for c in cands],
    }


def summarize(vals: list[float | None]) -> dict[str, Any]:
    xs = [v for v in vals if v is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": round(statistics.mean(xs), 4), "stdev": round(statistics.stdev(xs), 4) if len(xs) > 1 else 0.0,
            "min": min(xs), "max": max(xs), "values": xs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--template-repeats", type=int, default=3)
    ap.add_argument("--endpoint", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--digest", default="sha256:dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--seed-base", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--image", default="python:3.13-slim")
    ap.add_argument("--max-probes", type=int, default=32)
    ap.add_argument("--out", type=Path, default=REPO / "benchmarks" / "results" / "m4_model_eval.json")
    ap.add_argument("--provider", choices=("loopback", "openai_compatible"), default="loopback",
                    help="loopback = native Ollama API (the M4 arm); openai_compatible = the I-105 adapter against --base-url "
                         "(used only as a transport check against the same local Ollama server)")
    ap.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    args = ap.parse_args()
    if args.provider == "openai_compatible" and not args.base_url.startswith(("http://127.0.0.1", "http://[::1]", "http://localhost")):
        raise SystemExit("this benchmark only runs the OpenAI-compatible adapter against a loopback endpoint")
    model_arm = "local_model" if args.provider == "loopback" else "openai_compatible_loopback"

    from invariantguard import ENGINE_VERSION
    from invariantguard.semantic.prompt import PROMPT_EXAMPLE_FUNCTIONS, PROMPT_VERSION, SYSTEM_PROMPT, system_prompt_digest

    manifest = json.loads(MANIFEST.read_text())
    names = [f["symbol"].split("::")[1] for f in manifest["functions"]]
    leak = [n for n in names if n in PROMPT_EXAMPLE_FUNCTIONS or re.search(rf"\b{re.escape(n)}\b", SYSTEM_PROMPT)]
    if leak:
        raise SystemExit(f"held-out functions appear in the prompt: {leak}")
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result: dict[str, Any] = {
        "schema_version": "1.0", "benchmark": "m4_model_eval", "started_at": started, "engine_version": ENGINE_VERSION,
        "command": " ".join([".venv/bin/python", "benchmarks/model_eval.py", *sys.argv[1:]]),
        "heldout": {"manifest": str(MANIFEST.relative_to(REPO)), "functions": len(names), "seeded_bugs": sum(1 for f in manifest["functions"] if f["seeded_bug"]),
                    "seeded_bugs_dsl_expressible": sum(1 for f in manifest["functions"] if f["seeded_bug"] and f["seeded_bug"]["dsl_expressible"]),
                    "prompt_example_functions": list(PROMPT_EXAMPLE_FUNCTIONS), "overlap_with_prompt": leak},
        "prompt": {"version": PROMPT_VERSION, "system_prompt_digest": system_prompt_digest()},
        "settings": {"provider": args.provider, "endpoint": args.endpoint if args.provider == "loopback" else args.base_url,
                     "model": args.model, "model_digest_pinned": args.digest if args.provider == "loopback" else None,
                     "temperature": args.temperature, "seeds": [args.seed_base + (r if args.temperature > 0 else 0) for r in range(args.repeats)],
                     "max_probes": args.max_probes, "runner": f"docker {args.image}", "request_timeout_s": args.timeout},
        "host": {"model": sh(["sysctl", "-n", "hw.model"]), "cpu": sh(["sysctl", "-n", "machdep.cpu.brand_string"]),
                 "memory_bytes": int(sh(["sysctl", "-n", "hw.memsize"]) or 0), "os": f"macOS {sh(['sw_vers', '-productVersion'])} ({sh(['sw_vers', '-buildVersion'])})",
                 "python": platform.python_version(), "ollama": sh(["ollama", "--version"]), "docker": sh(["docker", "version", "--format", "{{.Server.Version}}"])},
        "arms": {"no_model_no_templates": {"note": "no discovery: 0 candidates, 0 checks, 0 counterexamples (trivial baseline)", "raw_candidates": 0}},
    }
    tmp = Path(tempfile.mkdtemp(prefix="ig-m4-eval-"))
    try:
        trs = []
        for r in range(args.template_repeats):
            root = make_workspace(tmp, args, args.seed_base)
            out, code, ms = run_discover(root, model=False)
            s = score(out, "template", manifest, root)
            s.update(exit_code=code, wall_ms=round(ms, 1), template_ms=out["timing_ms"]["templates"], validation_ms=out["timing_ms"]["validation"])
            trs.append(s)
            print(f"templates repeat {r}: {s['raw_candidates']} cands, refuted {s['refuted']}, wall {ms:.0f} ms", file=sys.stderr)
        result["arms"]["templates"] = {"repeats": trs}
        mrs = []
        for r in range(args.repeats):
            seed = args.seed_base + (r if args.temperature > 0 else 0)
            root = make_workspace(tmp, args, seed)
            with RssSampler() as rss:
                out, code, ms = run_discover(root, model=True)
            s = score(out, "local_model", manifest, root)
            fns = out["model"].get("functions", [])
            answered = [f for f in fns if f.get("status") == "ANSWERED"]
            s.update(exit_code=code, wall_ms=round(ms, 1), model_status=out["model"]["status"], model_reason=out["model"]["reason"],
                     model_identity=(out["model"].get("capabilities") or {}).get("identity"),
                     data_egress=out["model"].get("data_egress"),
                     request_options=[f.get("request_options") for f in fns],
                     functions_attempted=len(fns), functions_answered=len(answered),
                     model_ms_total=out["timing_ms"]["model"],
                     model_ms_per_function=summarize([f["elapsed_ms"] for f in answered]),
                     model_ms_per_candidate=round(out["timing_ms"]["model"] / s["raw_candidates"], 1) if s["raw_candidates"] else None,
                     prompt_tokens=summarize([f.get("prompt_tokens") for f in answered]),
                     output_tokens=summarize([f.get("output_tokens") for f in answered]),
                     output_tokens_per_s=summarize([round(f["output_tokens"] / (f["runtime_timing_ms"]["eval_ms"] / 1000), 2) for f in answered
                                                    if f.get("output_tokens") and f.get("runtime_timing_ms", {}).get("eval_ms")]),
                     done_reasons={d: sum(1 for f in answered if f.get("done_reason") == d) for d in {f.get("done_reason") for f in answered}},
                     response_sha256=[f.get("response_sha256") for f in fns],
                     inference_server_peak_rss_mb=round(rss.peak_kb / 1024, 1), rss_samples=rss.samples,
                     per_function=[{k: f.get(k) for k in ("symbol_id", "status", "elapsed_ms", "prompt_tokens", "output_tokens", "done_reason",
                                                         "response_rejection", "raw_candidates", "context_bytes", "snippets", "reason")} for f in fns],
                     template_candidates_same_run=sum(1 for c in out["candidates"] if c["source"] == "template"))
            mrs.append(s)
            print(f"model repeat {r}: status {s['model_status']}, {s['raw_candidates']} cands, valid {s['nonvacuous']}, refuted {s['refuted']}, "
                  f"exposed {s['seeded_bug_functions_exposed']}, wall {ms/1000:.0f} s", file=sys.stderr)
            result["arms"][model_arm] = {"repeats": mrs}
            args.out.write_text(json.dumps(result, indent=2, default=str) + "\n")  # partial results survive interruption
        agg = {}
        for arm in ("templates", model_arm):
            if arm not in result["arms"]:
                continue
            reps = result["arms"][arm]["repeats"]
            agg[arm] = {k: summarize([x.get(k) for x in reps]) for k in
                        ("raw_candidates", "schema_valid_rate", "nonvacuous_rate", "mechanically_checkable_rate", "distinct_validated", "refuted", "tested",
                         "unknown", "refuted_by_type_mismatch", "false_claims_refuted", "refuted_claim_valid_for_intended_behavior", "wall_ms",
                         "falsification_ms")}
            agg[arm]["seeded_bug_functions_exposed"] = [x["seeded_bug_functions_exposed"] for x in reps]
            if arm == model_arm:
                agg[arm].update({k: summarize([x.get(k) for x in reps]) for k in ("new_beyond_templates", "duplicate_of_earlier_claim", "model_ms_total",
                                                                                    "model_ms_per_candidate", "inference_server_peak_rss_mb")})
                agg[arm]["identical_responses_across_repeats"] = (len({tuple(x["response_sha256"]) for x in reps}) == 1) if len(reps) > 1 else None
        result["summary"] = agg
        result["guard_process_peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024), 1)
        result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result["ollama_ps_after"] = sh(["ollama", "ps"])
        args.out.write_text(json.dumps(result, indent=2, default=str) + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPO / "src"))
    raise SystemExit(main())
