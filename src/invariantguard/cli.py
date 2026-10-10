"""``guard`` command-line interface (argparse).

Implemented: ``guard check``, ``guard verify`` (M2; ``--execute --run-profile`` in M3),
``guard baseline create|list|show`` (M2), ``guard contracts list|show|declare|approve|
revise|reject|retire|import|discover`` (M3), ``guard runner setup|probe`` (M3),
``guard contracts discover --model local [--execute]`` and ``guard model status`` (M4),
``guard contracts discover --model remote``, ``guard contracts context|propose`` (I-105/I-106),
``guard mcp serve --stdio`` and ``guard explain FINDING_ID`` (M5), ``guard capabilities``,
``guard init``. Other commands from docs/09 belong to later
milestones and are not registered.

stdout carries only the requested output (JSON or the human report). Logs, progress and
errors go to stderr. Exit codes follow docs/09: 0 PASS/WARN, 1 BLOCK, 2 INCOMPLETE,
3 ERROR, 4 invalid usage/configuration.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Sequence

from . import ENGINE_VERSION
from .config import CONFIG_DIR, CONFIG_NAME, INIT_TEMPLATE, SUGGESTED_IGNORE
from .frontends.python import FRONTEND_CAPABILITIES
from .models.enums import EXIT_ERROR, EXIT_INCOMPLETE, EXIT_PASS, EXIT_USAGE
from .orchestration import CheckOptions, UsageError, run_check
from .snapshots.git import git_executable
from .solver.z3_backend import probe_z3


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        sys.stderr.write(f"guard: error: {message}\n")
        raise SystemExit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="guard", description="InvariantGuard: local verification for coding-agent patches (M5: Python static slice, change-aware verification, behavioral contracts with an isolated probe runner, local open-weight contract proposals, opt-in remote and agent contract proposers, stdio MCP server).")
    p.add_argument("--version", action="version", version=f"invariantguard {ENGINE_VERSION}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND", parser_class=_Parser)

    c = sub.add_parser("check", help="static single-snapshot analysis of PATH (never imports or executes it)")
    c.add_argument("path", type=Path, help="file or directory to analyze")
    c.add_argument("--format", choices=("json", "human"), default="human")
    c.add_argument("--root", type=Path, default=None, help="repository root (default: directory holding .guard/, else PATH)")
    c.add_argument("--config", type=Path, default=None, help="explicit .guard/config.toml")
    c.add_argument("--no-solver", action="store_true", help="disable the optional SMT solver for this run")
    c.add_argument("--timeout-ms", type=int, default=None, help="override per-query solver timeout")
    c.add_argument("--debug", action="store_true", help="print engine tracebacks to stderr")

    v = sub.add_parser("verify", help="compare an explicit base (Git REF or baseline:LABEL) with the captured working tree")
    v.add_argument("path", type=Path, nargs="?", default=Path("."), help="scope inside the repository (default: .)")
    v.add_argument("--base", required=True, help="REF or git:REF (a commit; nothing is checked out) or baseline:LABEL (saved baseline)")
    v.add_argument("--mode", choices=("quick", "full"), default="quick", help="quick: affected slice within quick_budget_ms; full: whole scope within full_budget_ms")
    v.add_argument("--format", choices=("json", "human"), default="human")
    v.add_argument("--root", type=Path, default=None)
    v.add_argument("--config", type=Path, default=None)
    v.add_argument("--no-solver", action="store_true")
    v.add_argument("--timeout-ms", type=int, default=None)
    v.add_argument("--no-cache", action="store_true", help="do not read or write the summary cache")
    v.add_argument("--state-dir", type=Path, default=None, help="state directory (default: <root>/.guard)")
    v.add_argument("--execute", action="store_true", help="also run dynamic contract checks in the configured runner (needs an enabled run profile)")
    v.add_argument("--run-profile", default=None, help="run profile from [execution.profiles] (default: [execution] profile)")
    v.add_argument("--replay", action="store_true", help="differential replay: run base and candidate on identical inputs (harvested from tests and doctests, "
                   "or generated in the runner) and report confirmed behavior changes of the changed symbols (needs --execute)")
    v.add_argument("--no-generate", action="store_true", help="with --replay: use harvested inputs only (no in-runner Hypothesis generation)")
    v.add_argument("--debug", action="store_true")

    ct = sub.add_parser("contracts", help="behavioral contracts: list, declare, approve, revise, reject, retire, import, discover")
    csub = ct.add_subparsers(dest="contracts_command", metavar="ACTION", parser_class=_Parser)

    def _common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--path", type=Path, default=Path("."), help="a path inside the repository (default: .)")
        sp.add_argument("--state-dir", type=Path, default=None)
        sp.add_argument("--root", type=Path, default=None)
        sp.add_argument("--config", type=Path, default=None)

    cl = csub.add_parser("list", help="current contracts, authority, lifecycle and evidence")
    cl.add_argument("--format", choices=("json", "human"), default="json")
    _common(cl)
    cs = csub.add_parser("show", help="one contract with all revisions, events and evidence")
    cs.add_argument("contract_id")
    _common(cs)
    cd = csub.add_parser("declare", help="record a maintainer-declared contract (authority DECLARED)")
    cd.add_argument("--file", type=Path, required=True)
    cd.add_argument("--reason", required=True)
    _common(cd)
    for action, text in (("approve", "INFERRED/REJECTED -> APPROVED"), ("reject", "INFERRED -> REJECTED (history kept)"), ("retire", "-> RETIRED (history kept)")):
        a = csub.add_parser(action, help=f"record an explicit authority change: {text}")
        a.add_argument("contract_id")
        a.add_argument("--reason", required=True)
        _common(a)
    cr = csub.add_parser("revise", help="create a new revision (stale evidence is not inherited)")
    cr.add_argument("contract_id")
    cr.add_argument("--file", type=Path, required=True)
    cr.add_argument("--reason", required=True)
    _common(cr)
    ci = csub.add_parser("import", help="index contract files that were not recorded through guard (explicit review step)")
    ci.add_argument("--reason", required=True)
    _common(ci)
    cdisc = csub.add_parser("discover", help="propose INFERRED candidates (templates; optionally a local model) and check them mechanically (never approves)")
    cdisc.add_argument("--templates", action="store_true", default=True, help="deterministic templates (always on)")
    cdisc.add_argument("--model", choices=("local", "remote"), default=None,
                       help="also ask the configured proposer model: local = loopback destinations only (nothing leaves this machine); "
                            "remote = the configured bring-your-own-key provider (context packets leave this machine). Candidates pass the same validation")
    cdisc.add_argument("--execute", action="store_true", help="falsify every validated candidate in the configured runner (needs an enabled run profile)")
    cdisc.add_argument("--run-profile", default=None, help="run profile for --execute (default: [execution] profile)")
    cdisc.add_argument("--symbol", action="append", default=None, help="limit discovery to this target symbol (repeatable)")
    cdisc.add_argument("--max-functions", type=int, default=None, help="cap on functions sent to the model (default: semantic.max_functions)")
    cdisc.add_argument("--show-context", action="store_true", help="include the exact context packets sent to the model in the output")
    cdisc.add_argument("--dry-run", action="store_true", help="validate and print, record nothing")
    _common(cdisc)
    cctx = csub.add_parser("context", help="print the exact bounded ContextPacket, candidate schema and DSL reference for one target (contacts nothing)")
    cctx.add_argument("symbol", help="target symbol, e.g. pkg/mod.py::fn")
    cctx.add_argument("--format", choices=("json",), default="json")
    _common(cctx)
    cprop = csub.add_parser("propose", help="submit agent-proposed candidates (JSON batch file); validated and optionally falsified, recorded INFERRED only")
    cprop.add_argument("--file", type=Path, required=True, help="one batch, a list of batches, or {\"batches\": [...]} (see `guard contracts context`)")
    cprop.add_argument("--client", default=None, metavar="NAME[/VERSION]", help="the proposing agent's self-reported identity (recorded in provenance)")
    cprop.add_argument("--execute", action="store_true", help="falsify accepted candidates in the configured runner (needs an enabled run profile)")
    cprop.add_argument("--run-profile", default=None)
    cprop.add_argument("--dry-run", action="store_true", help="validate and print, record nothing")
    _common(cprop)

    rn = sub.add_parser("runner", help="prepare or probe the isolated execution backend of a run profile")
    rsub = rn.add_subparsers(dest="runner_command", metavar="ACTION", parser_class=_Parser)
    for action, text in (("setup", "prepare the profile's environment explicitly (pulls the image; the only networked command)"),
                         ("probe", "verify the profile's isolation capabilities from inside the boundary (runs no repository code)")):
        r = rsub.add_parser(action, help=text)
        r.add_argument("--run-profile", default=None)
        r.add_argument("--path", type=Path, default=Path("."))
        r.add_argument("--root", type=Path, default=None)
        r.add_argument("--config", type=Path, default=None)

    md = sub.add_parser("model", help="inspect the configured local model (loopback endpoint only; never downloads)")
    msub = md.add_subparsers(dest="model_command", metavar="ACTION", parser_class=_Parser)
    ms = msub.add_parser("status", help="query the configured loopback endpoint for the model's identity, license and runtime")
    ms.add_argument("--path", type=Path, default=Path("."))
    ms.add_argument("--root", type=Path, default=None)
    ms.add_argument("--config", type=Path, default=None)

    b = sub.add_parser("baseline", help="save or inspect explicit, immutable baselines")
    bsub = b.add_subparsers(dest="baseline_command", metavar="ACTION", parser_class=_Parser)
    bc = bsub.add_parser("create", help="record an immutable baseline (never replaces an existing label)")
    bc.add_argument("path", type=Path, nargs="?", default=Path("."))
    bc.add_argument("--label", required=True)
    bc.add_argument("--reason", required=True, help="why this revision is the baseline (recorded)")
    bc.add_argument("--rev", default=None, help="record a Git commit instead of the working tree")
    bc.add_argument("--retain-source", action="store_true", help="store copies of the source under .guard/snapshots (enables full comparison without Git)")
    bc.add_argument("--supersedes", default=None, help="label of an earlier baseline this one replaces in intent (both are kept)")
    bc.add_argument("--root", type=Path, default=None)
    bc.add_argument("--config", type=Path, default=None)
    bc.add_argument("--no-solver", action="store_true")
    bc.add_argument("--state-dir", type=Path, default=None)
    bl = bsub.add_parser("list", help="list saved baselines")
    bl.add_argument("path", type=Path, nargs="?", default=Path("."))
    bl.add_argument("--state-dir", type=Path, default=None)
    bl.add_argument("--root", type=Path, default=None)
    bs = bsub.add_parser("show", help="show one saved baseline")
    bs.add_argument("label")
    bs.add_argument("path", type=Path, nargs="?", default=Path("."))
    bs.add_argument("--state-dir", type=Path, default=None)
    bs.add_argument("--root", type=Path, default=None)

    ex = sub.add_parser("explain", help="explain a finding from a recorded verify report (deterministic; reads stored evidence only)")
    ex.add_argument("finding_id")
    ex.add_argument("--run", default=None, help="run_id of the report (default: the newest report containing the finding)")
    ex.add_argument("--path", type=Path, default=Path("."), help="a path inside the repository (default: .)")
    ex.add_argument("--root", type=Path, default=None)
    ex.add_argument("--config", type=Path, default=None)
    ex.add_argument("--state-dir", type=Path, default=None)
    ex.add_argument("--format", choices=("json", "human"), default="human")

    mc = sub.add_parser("mcp", help="Model Context Protocol server for coding agents (optional 'mcp' extra)")
    mcsub = mc.add_subparsers(dest="mcp_command", metavar="ACTION", parser_class=_Parser)
    mcs = mcsub.add_parser("serve", help="serve verify_patch, check_scope, get_contracts, get_finding, explain_finding, get_context_packet, "
                                         "propose_contracts, get_capabilities")
    mcs.add_argument("--stdio", action="store_true", required=True, help="serve over stdin/stdout (the only transport)")
    mcs.add_argument("--root", dest="roots", action="append", default=[], metavar="[ALIAS=]PATH",
                     help="absolute repository root agents may verify (repeatable; alias defaults to the directory name)")
    mcs.add_argument("--allow-profile", dest="profiles", action="append", default=[], metavar="NAME",
                     help="preauthorize this isolated (docker) run profile for verify_patch(run_profile=NAME); its definition is pinned at startup")
    mcs.add_argument("--timeout-s", type=float, default=600.0, help="wall-clock limit per tool call (default 600)")

    k = sub.add_parser("capabilities", help="report installed adapters, solver, model and isolation capabilities")
    k.add_argument("--format", choices=("json", "human"), default="human")
    k.add_argument("--path", type=Path, default=None, help="also describe this repository's configured contract proposer and its data egress "
                                                           "(reads .guard/config.toml; contacts nothing)")
    k.add_argument("--config", type=Path, default=None)

    i = sub.add_parser("init", help="write a minimal .guard/config.toml (runs nothing)")
    i.add_argument("path", type=Path, nargs="?", default=Path("."))
    return p


def capabilities() -> dict[str, Any]:
    z3 = probe_z3()
    try:
        import jsonschema  # noqa: F401

        schema_validation = "available"
    except ImportError:
        schema_validation = "unavailable (install the 'dev' extra)"
    return {
        "schema_version": "1.0",
        "engine_version": ENGINE_VERSION,
        "python": sys.version.split()[0],
        "frontends": [FRONTEND_CAPABILITIES],
        "solver": {"name": "z3", "available": z3.available, "version": z3.version, "reason": z3.reason},
        "execution": _execution_capability(),
        "model": {"status": "available (explicit)",
                  "reason": "guard contracts discover --model local with [semantic] enabled (Ollama HTTP API on a validated loopback endpoint, or an "
                            "OpenAI-compatible endpoint on loopback; operations: propose_contracts; no download); check it with `guard model status`"},
        "remote_proposer": {"status": "available (explicit, opt-in)",
                            "reason": "guard contracts discover --model remote with [semantic] provider = openai_compatible|anthropic, base_url, model_id, "
                                      "api_key_env and remote_allowed = true (https only; context packets LEAVE the machine; never a fallback)"},
        "agent_proposer": {"status": "available",
                           "reason": "guard contracts context SYMBOL / guard contracts propose --file F, and MCP get_context_packet / propose_contracts "
                                     "(the calling agent proposes; candidates are validated identically and recorded INFERRED only)"},
        "mcp": _mcp_capability(),
        "patch_verification": {"status": "available" if git_executable() else "baselines only",
                               "reason": "guard verify --base REF|baseline:LABEL (static, quick/full); Git: " + (git_executable() or "not found (use baseline:LABEL)")},
        "schema_validation": schema_validation,
    }


def _mcp_capability() -> dict[str, Any]:
    from .mcp_server import sdk_version

    v = sdk_version()
    if v is None:
        return {"status": "unavailable", "reason": "the MCP SDK is not installed (pip install -e '.[mcp]'); guard mcp serve --stdio needs it"}
    return {"status": "available (explicit)",
            "reason": f"guard mcp serve --stdio --root [ALIAS=]PATH [--allow-profile NAME] (mcp {v}); tools: verify_patch, check_scope, get_contracts, "
                      "get_finding, explain_finding, get_context_packet, propose_contracts (INFERRED only), get_capabilities; no approval, baseline or command tools"}


def _execution_capability() -> dict[str, Any]:
    """Static description only: no daemon call here (use `guard runner probe` to verify a profile)."""
    from .execution.profiles import RunProfileConfig
    from .execution.runner import find_docker

    docker = find_docker(RunProfileConfig(name="x", backend="docker", allowed_targets=["x.py::*"]))
    return {"status": "available (explicit)",
            "reason": "guard verify --execute --run-profile NAME with [execution] enabled; backends: docker (capability-probed; "
                      + ("CLI at " + docker if docker else "docker CLI not found") + "), unisolated (only with allow_unisolated = true); "
                      "verify a profile with `guard runner probe`"}


def _human_capabilities(c: dict[str, Any]) -> str:
    s = c["solver"]
    lines = [
        f"invariantguard {c['engine_version']} (python {c['python']})",
        f"  frontend python: {c['frontends'][0]['parser']}, grammar {c['frontends'][0]['grammar']}",
        f"  solver z3: {'available ' + s['version'] if s['available'] else 'unavailable (' + str(s['reason']) + ')'}",
    ]
    for key in ("execution", "model", "remote_proposer", "agent_proposer", "mcp", "patch_verification"):
        lines.append(f"  {key}: {c[key]['status']} - {c[key]['reason']}")
    sp = c.get("configured_proposer")
    if sp is not None:
        if sp.get("error"):
            lines.append(f"  configured proposer: configuration error: {sp['error']}")
        else:
            lines.append(f"  configured proposer ({sp['config']}): {sp['summary']}")
            if sp.get("warning"):
                lines.append(f"    WARNING: {sp['warning']}")
    lines.append(f"  schema validation: {c['schema_validation']}")
    return "\n".join(lines)


def human_report(r: dict[str, Any]) -> str:
    cov = r["coverage"]
    if r["command"] == "verify":
        patch = r["patch"]
        head = [
            f"InvariantGuard verify ({patch['mode']})  verdict={r['verdict']}  run_outcome={r['run_outcome']}  policy={r['policy_id']}",
            f"base {patch['base']['spec']} -> {r['base_snapshot']}",
            f"candidate {r['candidate_snapshot']}",
            f"files: +{len(patch['files']['added'])} -{len(patch['files']['deleted'])} ~{len(patch['files']['modified'])} renamed {len(patch['files']['renamed'])}; "
            f"slice: {patch['slice']['affected_symbols']} symbol(s) affected, {patch['slice']['outside_slice_symbols']} outside",
        ]
    else:
        head = [
            f"InvariantGuard check  verdict={r['verdict']}  run_outcome={r['run_outcome']}  policy={r['policy_id']}",
            f"snapshot {r['candidate_snapshot']}  (patch relation: UNDETERMINED for single-snapshot checks)",
        ]
    out = head + [
        f"coverage: {cov['analyzed_symbols']}/{cov['requested_symbols']} symbols analyzed, "
        f"{cov['resolved_required_obligations']}/{cov['required_obligations']} required obligations resolved",
        f"solver: {r['capabilities']['solver']} ({r['capabilities']['solver_detail']})",
        "",
    ]
    for o in r["obligation_results"]:
        sp = o["source_span"]
        loc = f"{sp['path']}:{sp['start_line']}:{sp['start_column'] + 1}"
        scope = f" [{o['scope_validity']}]" if o["scope_validity"] else ""
        rel = f" {o['patch_relation']}" if o.get("patch_relation") else ""
        out.append(f"{loc}  {o['property_result']}{scope}{rel}  `{o['operation_text']}`  ({o['symbol_id'].split('::', 1)[1]}, {o['property_id']}, {o['method']})")
        if o["witness"] is not None:
            out.append(f"    witness: {o['witness']}{'' if o['witness_validated'] else ' (NOT validated)'}")
        if o["path_constraints"]:
            out.append(f"    path: {' AND '.join(o['path_constraints'])}")
        out.append(f"    domain: {o['domain']}")
        for a in o["assumptions"]:
            out.append(f"    assumes: {a}")
        for why in o["reasons"]:
            out.append(f"    why: {why}")
    for f in r["findings"]:
        if f["patch_relation"] in ("RESOLVED",) or f["message"].startswith("base finding"):
            out.append(f"BASE {f['patch_relation']}: {f['message']}")
    if "contracts" in r:
        ex = r["contracts"]["execution"]
        out.append(f"contracts: execution {ex['status']}" + (f" ({ex['backend']}, profile {ex['profile']})" if ex["requested"] else "") + f": {ex['reason']}")
        for c in r["contracts"]["results"]:
            rel = f" {c['patch_relation']}" if c["patch_relation"] else ""
            out.append(f"  {c['contract_id']} r{c['revision']} [{c['authority']}] {c['status']} {c['property_result']}{rel}"
                       + (f" severity={c['severity']}" if c["severity"] else ""))
            if c.get("witness") is not None:
                out.append(f"    witness: {c['witness']}" + (f" (minimized from {c['original_witness']})" if c.get("original_witness") not in (None, c["witness"]) else ""))
            for why in c["reasons"][-2:]:
                out.append(f"    why: {why}")
    for s in cov["skipped_checks"]:
        out.append(f"SKIPPED {s['kind']}: {s['path']}: {s['reason']}")
    for e in r["errors"]:
        out.append(f"ENGINE ERROR [{e['stage']}] {e['symbol_id']}: {e['error_type']}: {e['message']}")
    out.append("")
    out.append("verdict reasons:")
    out.extend(f"  - {x}" for x in r["verdict_reasons"])
    return "\n".join(out)


def _cmd_check(args: argparse.Namespace) -> int:
    opts = CheckOptions(path=args.path, root=args.root, config_path=args.config, no_solver=args.no_solver, timeout_ms=args.timeout_ms)
    try:
        result = run_check(opts)
    except UsageError as exc:
        sys.stderr.write(f"guard: {exc}\n")
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 - engine failure outside per-symbol isolation
        sys.stderr.write(f"guard: engine error: {type(exc).__name__}: {exc}\n")
        if args.debug:
            traceback.print_exc(file=sys.stderr)
        return EXIT_ERROR
    return _emit_report(result, args.format)


def _emit_report(result, fmt: str) -> int:
    for line in result.log:
        sys.stderr.write(f"guard: {line}\n")
    try:
        from .models.schema import SchemaValidationUnavailable, validation_errors

        errs = validation_errors("report", result.report)
        if errs:
            sys.stderr.write("guard: engine error: generated report violates report.schema.json:\n" + "\n".join("  " + e for e in errs[:20]) + "\n")
            return EXIT_ERROR
    except SchemaValidationUnavailable:
        sys.stderr.write("guard: note: jsonschema not installed; report not self-validated\n")
    if fmt == "json":
        sys.stdout.write(json.dumps(result.report, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(human_report(result.report) + "\n")
    sys.stderr.write(f"guard: verdict {result.report['verdict']} (exit {result.exit_code})\n")
    return result.exit_code


def _cmd_verify(args: argparse.Namespace) -> int:
    from .verify import EngineFailure, VerifyOptions, run_verify

    opts = VerifyOptions(base=args.base, mode=args.mode, path=args.path, root=args.root, config_path=args.config, no_solver=args.no_solver,
                         timeout_ms=args.timeout_ms, no_cache=args.no_cache, state_dir=args.state_dir, execute=args.execute,
                         run_profile=args.run_profile, replay=args.replay, replay_generate=not args.no_generate)
    try:
        result = run_verify(opts)
    except UsageError as exc:
        sys.stderr.write(f"guard: {exc}\n")
        return EXIT_USAGE
    except EngineFailure as exc:
        sys.stderr.write(f"guard: engine error: {exc}\n")
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - engine failure outside per-symbol isolation
        sys.stderr.write(f"guard: engine error: {type(exc).__name__}: {exc}\n")
        if args.debug:
            traceback.print_exc(file=sys.stderr)
        return EXIT_ERROR
    return _emit_report(result, args.format)


def _cmd_baseline(args: argparse.Namespace) -> int:
    from .verify import BaselineOptions, EngineFailure, baseline_list, run_baseline_create

    try:
        if args.baseline_command == "create":
            out, log = run_baseline_create(BaselineOptions(label=args.label, reason=args.reason, path=args.path, rev=args.rev,
                                                           retain_source=args.retain_source, supersedes=args.supersedes, root=args.root,
                                                           config_path=args.config, no_solver=args.no_solver, state_dir=args.state_dir))
            for line in log:
                sys.stderr.write(f"guard: {line}\n")
            sys.stdout.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
            sys.stderr.write(f"guard: recorded baseline {out['baseline']['label']} ({out['baseline']['snapshot_id'][:19]}…)\n")
            return EXIT_PASS
        if args.baseline_command in ("list", "show"):
            items = baseline_list(args.path, args.state_dir, args.root)
            if args.baseline_command == "show":
                found = [b for b in items if b["label"] == args.label]
                if not found:
                    sys.stderr.write(f"guard: no baseline named {args.label!r}\n")
                    return EXIT_USAGE
                sys.stdout.write(json.dumps(found[0], indent=2) + "\n")
            else:
                sys.stdout.write(json.dumps({"baselines": items}, indent=2) + "\n")
            return EXIT_PASS
    except UsageError as exc:
        sys.stderr.write(f"guard: {exc}\n")
        return EXIT_USAGE
    except EngineFailure as exc:
        sys.stderr.write(f"guard: engine error: {exc}\n")
        return EXIT_ERROR
    sys.stderr.write("guard: baseline needs an action: create, list or show\n")
    return EXIT_USAGE


def _human_contracts(out: dict[str, Any]) -> str:
    lines = []
    for c in out["contracts"]:
        ev = c["latest_evidence"]
        lines.append(f"{c['contract_id']} r{c['revision']}  {c['authority']}  {c['lifecycle_state']}  {c['scope']}  ({c['input_profile']})"
                     + (f"  latest evidence: {ev['result_label']} ({ev['side']}, {ev['created_at']})" if ev else "  no evidence for this revision")
                     + (f"  [{c['stale_evidence_from_other_revisions']} stale]" if c["stale_evidence_from_other_revisions"] else "")
                     + ("" if c["integrity"] == "ok" else "  INTEGRITY ERROR: " + "; ".join(c["integrity_errors"])))
    for d in out["diagnostics"]:
        lines.append(f"note: {d['message']}")
    return "\n".join(lines) if lines else "no contracts"


def _cmd_contracts(args: argparse.Namespace) -> int:
    from .contracts import commands as cc

    action = args.contracts_command
    if action is None:
        sys.stderr.write("guard: contracts needs an action: list, show, declare, approve, revise, reject, retire, import, discover, context or propose\n")
        return EXIT_USAGE
    if action == "context":  # read-only: never opens (or creates) the state store
        from .orchestration import resolve_inputs

        try:
            root, cfg = resolve_inputs(CheckOptions(path=args.path, root=args.root, config_path=args.config))
            out = cc.cmd_context(root, cfg, args.symbol, args.state_dir.resolve() if args.state_dir else None)
        except UsageError as exc:
            sys.stderr.write(f"guard: {exc}\n")
            return EXIT_USAGE
        sys.stdout.write(cc.dumps(out) + "\n")
        return EXIT_PASS
    try:
        ctx = cc.open_ctx(args.path, args.state_dir, args.root, args.config)
    except UsageError as exc:
        sys.stderr.write(f"guard: {exc}\n")
        return EXIT_USAGE
    try:
        if action == "list":
            out = cc.cmd_list(ctx)
            sys.stdout.write((cc.dumps(out) if args.format == "json" else _human_contracts(out)) + "\n")
            return EXIT_PASS
        if action == "show":
            out = cc.cmd_show(ctx, args.contract_id)
        elif action == "declare":
            out = cc.cmd_declare(ctx, args.file, args.reason)
        elif action in ("approve", "reject", "retire"):
            out = cc.cmd_authority(ctx, action, args.contract_id, args.reason)
        elif action == "revise":
            out = cc.cmd_revise(ctx, args.contract_id, args.file, args.reason)
        elif action == "import":
            out = cc.cmd_import(ctx, args.reason)
        elif action == "propose":
            try:
                raw = json.loads(args.file.read_bytes().decode("utf-8"), parse_constant=_no_constant)
            except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
                raise UsageError(f"cannot read candidate batches from {args.file}: {type(exc).__name__}: {str(exc)[:200]}") from None
            name, _, version = (args.client or "guard-cli").partition("/")
            out, code = cc.cmd_propose(ctx, raw, client={"name": name, "version": version}, execute=args.execute, run_profile=args.run_profile,
                                       dry_run=args.dry_run)
            for line in out.get("log", []):
                sys.stderr.write(f"guard: {line}\n")
            sys.stdout.write(cc.dumps(out) + "\n")
            for r in out["incomplete_reasons"]:
                sys.stderr.write(f"guard: INCOMPLETE: {r}\n")
            sys.stderr.write(f"guard: {out['summary']['accepted']} of {out['summary']['candidates']} agent candidate(s) accepted (INFERRED; not approved)\n")
            return code
        else:
            if args.max_functions is not None and args.max_functions < 1:
                raise UsageError("--max-functions must be >= 1")
            out, code = cc.cmd_discover(ctx, cc.DiscoverOptions(dry_run=args.dry_run, model=args.model, execute=args.execute,
                                                                run_profile=args.run_profile, symbols=args.symbol,
                                                                max_functions=args.max_functions, show_context=args.show_context))
            for line in out.get("log", []):
                sys.stderr.write(f"guard: {line}\n")
            sys.stdout.write(cc.dumps(out) + "\n")
            for r in out["incomplete_reasons"]:
                sys.stderr.write(f"guard: INCOMPLETE: {r}\n")
            for r in out.get("engine_errors", []):
                sys.stderr.write(f"guard: ERROR: falsification engine fault: {r}\n")
            return code
        sys.stdout.write(cc.dumps(out) + "\n")
        if action in ("approve", "reject", "retire", "declare", "revise"):
            sys.stderr.write(f"guard: recorded {action} for {out.get('contract_id') or out.get('declared')}\n")
        return EXIT_PASS
    except UsageError as exc:
        sys.stderr.write(f"guard: {exc}\n")
        return EXIT_USAGE
    finally:
        ctx.close()


def _no_constant(name: str) -> Any:
    raise ValueError(f"non-finite JSON constant {name}")


def _cmd_runner(args: argparse.Namespace) -> int:
    from .contracts import commands as cc

    if args.runner_command is None:
        sys.stderr.write("guard: runner needs an action: setup or probe\n")
        return EXIT_USAGE
    try:
        if args.runner_command == "setup":
            out = cc.cmd_runner_setup(args.path, args.root, args.run_profile, args.config)
            sys.stdout.write(cc.dumps(out) + "\n")
            return EXIT_PASS
        out, ok = cc.cmd_runner_probe(args.path, args.root, args.run_profile, args.config)
        sys.stdout.write(cc.dumps(out) + "\n")
        if not ok:
            sys.stderr.write(f"guard: RUNNER_UNAVAILABLE: {out['reason']}\n")
        return EXIT_PASS if ok else EXIT_INCOMPLETE
    except UsageError as exc:
        sys.stderr.write(f"guard: {exc}\n")
        return EXIT_USAGE


def _cmd_model(args: argparse.Namespace) -> int:
    """``guard model status``: contacts only the configured loopback endpoint (read-only requests)."""
    from .contracts.commands import dumps
    from .orchestration import resolve_inputs
    from .semantic.discovery import make_reasoner
    from .semantic.endpoint import EndpointError

    if args.model_command != "status":
        sys.stderr.write("guard: model needs an action: status\n")
        return EXIT_USAGE
    try:
        _, cfg = resolve_inputs(CheckOptions(path=args.path, root=args.root, config_path=args.config))
    except UsageError as exc:
        sys.stderr.write(f"guard: {exc}\n")
        return EXIT_USAGE
    sem = cfg.semantic
    if not sem.get("enabled"):
        sys.stderr.write("guard: [semantic] is not enabled; no model is configured (nothing was contacted)\n")
        return EXIT_USAGE
    from .semantic.reasoner import ReasonerUnavailable

    try:
        caps = make_reasoner(sem).capabilities()
    except (EndpointError, ReasonerUnavailable) as exc:
        sys.stderr.write(f"guard: {exc}\n")
        return EXIT_USAGE
    sys.stdout.write(dumps({"schema_version": "1.0", "configured": {k: sem.get(k) for k in ("provider", "api", "endpoint", "base_url", "model_id", "model_digest",
                                                                                             "api_key_env", "remote_allowed") if k in sem},
                            **caps.to_json()}) + "\n")
    if not caps.available:
        sys.stderr.write(f"guard: model unavailable: {caps.reason}\n")
    return EXIT_PASS if caps.available else EXIT_INCOMPLETE


def _cmd_explain(args: argparse.Namespace) -> int:
    from .orchestration import resolve_inputs
    from .service import ServiceError, contract_definition, evidence_freshness, explain, explanation_text, finding_detail, lookup_finding

    try:
        root, _ = resolve_inputs(CheckOptions(path=args.path, root=args.root, config_path=args.config))
        state_dir = args.state_dir.resolve() if args.state_dir is not None else None
        found = lookup_finding(root, args.finding_id, args.run, state_dir=state_dir)
        fresh = evidence_freshness(root, found, args.config.resolve() if args.config else None, state_dir=state_dir)
    except UsageError as exc:
        sys.stderr.write(f"guard: {exc}\n")
        return EXIT_USAGE
    except ServiceError as exc:
        sys.stderr.write(f"guard: {exc.status}: {exc.message}\n")
        return EXIT_USAGE if exc.status in ("INVALID_REQUEST", "NOT_FOUND") else EXIT_ERROR
    detail = finding_detail(found)
    detail["contract_definition"] = contract_definition(root, detail.get("contract_result"), args.config.resolve() if args.config else None, state_dir=state_dir)
    exp = explain(detail, fresh)
    if args.format == "json":
        sys.stdout.write(json.dumps({"schema_version": "1.0", "finding_id": args.finding_id, "run": detail["run"], "freshness": fresh, "explanation": exp,
                                     "finding": detail["finding"], "evidence": detail["evidence"]}, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(explanation_text(exp) + "\n")
    if fresh["evidence_status"] != "CURRENT":
        sys.stderr.write(f"guard: evidence {fresh['evidence_status']}: {fresh.get('message', '')}\n")
    return EXIT_PASS


def _cmd_mcp(args: argparse.Namespace) -> int:
    if args.mcp_command != "serve":
        sys.stderr.write("guard: mcp needs an action: serve --stdio\n")
        return EXIT_USAGE
    from .mcp_server import MCPUnavailable, serve_stdio
    from .service import AgentService

    try:
        service = AgentService.from_specs(args.roots, args.profiles, max_timeout_s=args.timeout_s)
    except UsageError as exc:
        sys.stderr.write(f"guard: {exc}\n")
        return EXIT_USAGE
    try:
        return serve_stdio(service)
    except MCPUnavailable as exc:
        sys.stderr.write(f"guard: {exc}\n")
        return EXIT_USAGE
    except KeyboardInterrupt:
        return EXIT_PASS


def configured_proposer(sem: dict[str, Any], config_path: Any) -> dict[str, Any]:
    """Static description of the configured contract proposer and its data egress (contacts nothing)."""
    from .semantic.remote import REMOTE_WARNING, DestinationError, validate_provider_url

    provider = sem.get("provider", "loopback")
    out: dict[str, Any] = {"config": str(config_path), "enabled": bool(sem.get("enabled")), "provider": provider, "model_id": sem.get("model_id")}
    if provider == "loopback":
        out.update(host=sem.get("endpoint"), context_leaves_machine=False, warning=None,
                   summary=f"loopback Ollama {sem.get('model_id') or '(no model_id)'} at {sem.get('endpoint') or '(no endpoint)'}; nothing leaves this machine")
        return out
    try:
        dest = validate_provider_url(sem.get("base_url"), remote_allowed=bool(sem.get("remote_allowed")))
    except DestinationError as exc:
        return {**out, "error": str(exc)}
    out.update(host=dest.host_label, base_url=dest.display, api_key_env=sem.get("api_key_env"), remote_allowed=bool(sem.get("remote_allowed")),
               context_leaves_machine=not dest.loopback, warning=None if dest.loopback else REMOTE_WARNING,
               summary=f"{provider} {sem.get('model_id')} at {dest.display}; " + ("loopback destination" if dest.loopback else
                       "REMOTE: `--model remote` sends bounded context packets (digest and byte size recorded per packet) to this host"))
    return out


def _cmd_capabilities(args: argparse.Namespace) -> int:
    c = capabilities()
    if args.path is not None or args.config is not None:
        from .config import ConfigError, find_config, load_config

        cfg_path = args.config or find_config((args.path or Path(".")).resolve())
        if cfg_path is not None:
            try:
                c["configured_proposer"] = configured_proposer(load_config(cfg_path).semantic, cfg_path)
            except ConfigError as exc:
                c["configured_proposer"] = {"config": str(cfg_path), "error": str(exc)}
    if args.format == "json":
        sys.stdout.write(json.dumps(c, indent=2) + "\n")
    else:
        sys.stdout.write(_human_capabilities(c) + "\n")
    return EXIT_PASS


def _cmd_init(args: argparse.Namespace) -> int:
    root: Path = args.path
    if not root.is_dir():
        sys.stderr.write(f"guard: not a directory: {root}\n")
        return EXIT_USAGE
    target = root / CONFIG_DIR / CONFIG_NAME
    if target.exists():
        sys.stderr.write(f"guard: {target} already exists; not overwritten\n")
        return EXIT_USAGE
    target.parent.mkdir(exist_ok=True)
    with open(target, "x", encoding="utf-8") as fh:
        fh.write(INIT_TEMPLATE)
    sys.stderr.write(f"guard: wrote {target}\n")
    sys.stdout.write(SUGGESTED_IGNORE)
    return EXIT_PASS


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    if args.command == "check":
        return _cmd_check(args)
    if args.command == "verify":
        return _cmd_verify(args)
    if args.command == "baseline":
        return _cmd_baseline(args)
    if args.command == "contracts":
        return _cmd_contracts(args)
    if args.command == "runner":
        return _cmd_runner(args)
    if args.command == "model":
        return _cmd_model(args)
    if args.command == "explain":
        return _cmd_explain(args)
    if args.command == "mcp":
        return _cmd_mcp(args)
    if args.command == "capabilities":
        return _cmd_capabilities(args)
    if args.command == "init":
        return _cmd_init(args)
    parser.error(f"unknown command {args.command}")
    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
