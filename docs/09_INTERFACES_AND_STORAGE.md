# 09. Interfaces and storage

## Intended CLI

These commands are proposed interfaces to implement milestone by milestone, not an existing installation.

| Command | Behavior |
| --- | --- |
| guard init | Write minimal local configuration and ignore suggestions; run nothing |
| guard capabilities | Report installed adapters, solver, model and isolation capabilities |
| guard check PATH --format json | Static single-snapshot analysis |
| guard baseline create --label NAME | Save explicit immutable baseline metadata |
| guard verify --base REF --mode quick --format json | Compare base with captured working tree |
| guard verify --base REF --mode full | Broaden static scope with separate budget |
| guard verify --base REF --execute --run-profile NAME | Add explicitly configured dynamic contract checks |
| guard contracts discover --model local | Propose and mechanically check candidates; do not approve |
| guard contracts list | Show current contracts, evidence and authority |
| guard contracts approve ID --reason TEXT | Record explicit authority change |
| guard contracts revise ID --file PATH --reason TEXT | Create contract revision; invalidate stale evidence |
| guard contracts reject ID --reason TEXT | Retain history while excluding candidate from policy |
| guard explain FINDING_ID | Read existing evidence and explain it |
| guard replay FINDING_ID --run-profile NAME | Reproduce stored witness through authorized runner |
| guard mcp serve --stdio | Serve check/read tools over stdio |

Baseline creation must specify source identity and whether replay-source retention is enabled. Static baseline metadata alone does not imply dynamic replay availability. `--mode full` never enables execution implicitly.

`guard check` has no patch attribution; its relation is UNDETERMINED. `guard verify` requires an explicit Git reference or saved baseline.

## Exit and output contract

| Exit | Meaning |
| --- | --- |
| 0 | PASS or WARN, with complete required coverage |
| 1 | BLOCK; report may also contain incomplete coverage |
| 2 | INCOMPLETE with no higher-priority block/error |
| 3 | ERROR in verifier/tool environment |
| 4 | Invalid CLI usage or invalid configuration |

Return structured JSON on stdout when requested. Human diagnostics and progress go to stderr. Do not mix logs, model tokens or stack traces into JSON. Make output ordering deterministic apart from explicit run IDs/timing fields.

## Report v1 example

This is illustrative, with symbolic IDs and example durations. Implementation must generate these values from real analysis.

```json
{
  "schema_version": "1.0",
  "run_id": "run-example",
  "base_snapshot": "sha256:base-example",
  "candidate_snapshot": "sha256:candidate-example",
  "run_outcome": "COMPLETE",
  "verdict": "BLOCK",
  "policy_id": "default-v1",
  "coverage": {
    "requested_symbols": 2,
    "analyzed_symbols": 2,
    "required_obligations": 2,
    "resolved_required_obligations": 2,
    "unknown_obligations": [],
    "skipped_checks": []
  },
  "timing_ms": {"capture": 0, "static": 0, "dynamic": 0, "model": 0},
  "findings": [
    {
      "finding_id": "finding-example",
      "symbol_id": "demo/math_ops.py::ratio",
      "property_id": "division.zero_denominator",
      "property_result": "PROVED_UNSAFE",
      "scope_validity": "EXACT_MODEL",
      "authority": "DECLARED",
      "patch_relation": "INTRODUCED",
      "severity": "ERROR",
      "source_span": {
        "path": "demo/math_ops.py",
        "start_line": 3,
        "start_column": 11,
        "end_line": 3,
        "end_column": 22,
        "snapshot_id": "sha256:candidate-example"
      },
      "quantifier": "exists admitted function input",
      "domain": "exact Python built-in integers",
      "assumptions": ["domain explicitly admitted in configuration"],
      "path_constraints": ["a == b", "d == a - b", "d == 0"],
      "witness": {"a": 1, "b": 1},
      "evidence_ids": ["evidence-example"],
      "replay_available": false
    }
  ],
  "obligation_results": [],
  "capabilities": {"solver": "configured", "execution": "disabled", "model": "disabled"}
}
```

Production schemas must reject placeholder IDs in stored results. The example shows a function-domain failure, not a claim that a particular production caller reaches it. Store successful obligations as well as findings.

## MVP schema checklist

Create machine-validated schemas for Report, Finding, ObligationResult, Evidence, SourceSpan, Snapshot, ContractRevision, CandidateBatch, ContextPacket, RunProfile and AnalysisPlan. This kit defines the required fields and enums; M0 creates the actual JSON Schema files and validation tests.

Allow optional forward fields only through an explicit schema-version policy. Unknown enum values are not silently interpreted as safe. Fields documenting scope/domain and coverage remain required.

Source locations use one-based lines and zero-based UTF-8 byte columns, with exclusive end positions. Python AST byte offsets must be normalized carefully. Kotlin adapters must convert their native offset conventions to the same schema.

## Configuration draft

```toml
schema_version = "1.0"

[analysis]
languages = ["python"]
mode = "quick"
solver_enabled = true
per_query_timeout_ms = 100
quick_budget_ms = 2000
require_coverage = true

[execution]
enabled = false
profile = ""
allow_unisolated = false

[semantic]
enabled = false
provider = "loopback"
endpoint = "http://127.0.0.1:PORT"
model_id = ""
download_during_scan = false

[privacy]
telemetry = false
store_raw_model_context = false
retain_replay_source = false
store_witnesses = true

[scope]
exclude = [".git", ".guard", ".venv", "vendor", "node_modules"]

[[domains]]
symbol = "demo/math_ops.py::ratio"
profile = "python_exact_builtin_ints"
authority = "DECLARED"
reason = "demo public API admits exact built-in integer inputs"
```

The endpoint is an unset illustrative placeholder; `guard init` should omit it or leave an empty value until a real endpoint is configured. Never attempt to connect to this example literally.

## MCP integration

Expose `verify_patch`, `check_scope`, `get_contracts`, `get_finding`, `explain_finding` and `get_capabilities`. They call the same orchestration code as the CLI and return versioned structured reports.

`verify_patch` inputs: repository alias/root restricted to configured roots, base reference, mode and optional preauthorized execution profile. Return report identity plus compact findings and coverage; allow fetching detailed evidence by ID.

Do not expose baseline replacement, contract approval or arbitrary command execution as default agent tools. Cancellation, timeouts and stale snapshot errors have explicit responses. Use stdio for local integration; stdout is reserved for protocol messages.

An agent convention can request verification before completion. MCP alone cannot force every agent to comply. Enforced acceptance requires client/hook/CI integration, which must be tested separately.

## Persistent layout

```text
.guard/
  config.toml
  state.sqlite
  contracts/       # approved declarations/revisions in reviewable JSON
  artifacts/       # content-addressed evidence and optional replay data
  snapshots/       # manifests; source only if retention enabled
  cache/
  runs/
```

SQLite owns identity, relationships, indexes and run transactions. Artifacts are immutable by digest. Contracts retain readable JSON revisions; avoid making SQLite and files competing editable sources of truth. Use explicit import/export and atomic revision transactions.

Recommended ignore rules exclude cache, local run output, sensitive artifacts and retained source. Whether approved contracts/configuration belong in Git is a maintainer choice; never commit these automatically. Implement schema migrations before writing a second schema version.
