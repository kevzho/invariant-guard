# Alpha release checklist (T55)

This maps every docs/11 gate criterion for M0-M5, and the docs/11 "Before an alpha" release criteria, to the evidence in this repository or to a stated gap. Evidence references point to `docs/IMPLEMENTATION_STATUS.md` (STATUS), `docs/DECISIONS.md` (I-xxx) and tests. Every listed test passes in the final suite run recorded in STATUS ("Validation commands"). Status values:

- **MET**: implemented and evidenced.
- **MET (limits)**: met, with disclosed limits.
- **PARTIAL**: part of the criterion is evidenced; the rest is a stated gap.
- **OPEN**: not done.

Verdict: the M0-M5 gates are met with the limits below. **The alpha release criteria are not all met.** One real client repair integration is PARTIAL: the recorded loop's agent is a local-model harness over MCP, and the Claude Code configuration is tested only by launching its documented command with the SDK client. `guard replay` is not implemented. No release has been made or published.

## M0: foundation

| Criterion | Status | Evidence |
| --- | --- | --- |
| Package installs in a clean local environment | MET | STATUS T00; `pip install -r requirements.lock && pip install -e . --no-deps`. The lock is regenerated with the `mcp` extra and equals `pip freeze --exclude-editable`. |
| Schema invalid/unknown cases fail validation | MET | STATUS T01; `tests/unit/test_schemas.py` |
| Tests run without model/network | MET | `tests/conftest.py` makes any in-process socket connect fail a test. The real-model test is opt-in (`IG_REAL_MODEL=1`). Docker tests skip with a reason when no daemon is reachable. |
| Source maps and snapshot identity represented | MET | `SourceSpan`, `snapshot.schema.json`, content-addressed `snapshot_id` |

## M1: single-snapshot static loop

| Criterion | Status | Evidence |
| --- | --- | --- |
| safe/unsafe/unknown fixtures produce actual semantic outcomes | MET | STATUS T10-T17; `tests/semantic_fixtures/` |
| No repository import occurs | MET | side-effect sentinel tests (T10) |
| Solver unknown and engine error are distinct | MET | TIMEOUT/RESOURCE_LIMIT give UNKNOWN (exit 2); injected faults give ERROR (exit 3) |
| A conditional domain never becomes unconditional through annotations | MET | ANNOTATION gives CONDITIONAL. Also seen through MCP: `test_config_is_read_only_from_the_root_never_a_parent` |
| Concrete witness for unsafe ratio; guarded safe obligation | MET | STATUS "Validation commands". Recorded run: witness `{a: 0, b: 0}` validated; after the repair the guard gives PROVED_SAFE [EXACT_MODEL]. |

## M2: patch verification

| Criterion | Status | Evidence |
| --- | --- | --- |
| Unchanged callers rechecked when a callee changes | MET | T22; `examples/patch_demo` |
| New/untracked/deleted files participate | MET | T20 |
| Incremental and full required obligations agree | MET | T28 (0 mismatches, 21 scenarios + 100 random seeds) |
| Stale caches fail closed | MET | T24. M5 adds: cancelled and timed-out runs publish no cache entries (`test_cancelled_verify_publishes_no_cache_entries_evidence_or_report`, `test_server_timeout_is_an_explicit_response_and_publishes_nothing`). |
| No checkout or working-tree mutation | MET | T27 (byte-, mode- and mtime-identical); MCP tools likewise (`test_only_configured_roots_and_inside_paths_are_served`) |
| Static timing and memory measured | MET | STATUS "M2 measurements" |
| Unsupported dynamic edges captured | MET | `patch.graph.unresolved_dynamic_edges` (I-042) |

## M3: behavioral contracts

| Criterion | Status | Evidence |
| --- | --- | --- |
| normalize regression: baseline pass / candidate fail on the same positive-sum input | MET | T35. Recorded run: `xs=[1]` (minimized from `[1.0, 1.0]`); base `[1.0]`, candidate `[0.5]`; Docker environment digest recorded. |
| Invalid-domain and both-fail cases are not regressions | MET | all-zero input excluded; both-fail is PRE_EXISTING (T35/T37) |
| Passing probes say TESTED | MET | recorded run final: `normalize.sum.v1` TESTED (finite probes, "not proofs") |
| Runner unavailable means INCOMPLETE when dynamic checks are required | MET | T33. A trial M5 run saw a real RUNNER_UNAVAILABLE (daemon timed out under load), which gave INCOMPLETE with no fallback (STATUS "M5 repair loop"). |

## M4: open-weight semantic layer

| Criterion | Status | Evidence |
| --- | --- | --- |
| Real model candidates enter the same mechanical pipeline | MET | T43/T44. Recorded run step 10: qwen2.5-coder:7b candidates were 2 VACUOUS_PREDICATE and 1 REFUTED in Docker; the template candidate was TESTED. |
| Invalid/vacuous/adversarial suggestions rejected | MET | `tests/unit/test_model_candidates.py` (42) |
| No cloud/network fallback | MET | T41 (loopback-only transport). The MCP server process opens no socket (`test_server_process_opens_no_network_connection`). The opt-in remote adapters (I-105) are never a fallback: `--model local` refuses non-loopback destinations, a failed remote provider is INCOMPLETE, and remote use needs `remote_allowed = true`, https and an explicit `--model remote`. |
| Model-off remains functional | MET | T45. Recorded run step 9: a no-model `guard check`. |
| One actual open-weight artifact with tested license and runtime identity | MET (limits) | qwen2.5-coder:7b `sha256:dae161e2…`, Apache-2.0 text digest, Ollama 0.35.1. Only one model was evaluated; a second needs an unauthorized download (I-089). |

## M5: agent-native alpha

| Criterion | Status | Evidence |
| --- | --- | --- |
| stdio MCP over shared APIs | MET | T50 (`service.py`, `mcp_server.py`; I-091/I-092); `tests/integration/test_mcp_server.py` (8) and `test_agent_service.py` (13) |
| Protocol output/log separation | MET | `test_stdout_carries_only_protocol_frames` (stray print, `sys.__stdout__`, child process on fd 1) |
| Restricted roots, tools and execution profiles; no approval/baseline tools | MET | T51 (I-093); six tools in M5, eight since I-106 (`get_context_packet` is read-only; `propose_contracts` records INFERRED candidates only and falsifies only through a preauthorized docker profile), closed schemas, preauthorized and pinned docker profiles. The M4/M5 audit found and fixed a check-to-use gap in the profile pin (A5-02, I-103) and a state file created by the read-only `get_contracts` (A5-01) |
| Cancellation and compact evidence retrieval | MET (limits) | T52 (I-094/I-096): client cancel, server timeout, stale snapshot, `get_finding`/`explain_finding`. Limit: no safe point inside one snapshot's lowering or one function's interval analysis. |
| One tested client integration documented | MET (limits) | T53: Claude Code project `.mcp.json` in `docs/MCP_INTEGRATION.md`; the test launches exactly that command (handshake, `tools/list`, `verify_patch`). A live Claude Code session was not tested. |
| patch -> verify -> failure evidence -> repair -> verify demonstrated | MET (limits) | T54: `examples/demo_repo/recorded_run/` (BLOCK, then one model repair, then PASS). The agent is a local-model harness over MCP, not Claude Code. The ratio repair returns None, which the declared properties do not cover. |
| The report tracks the final snapshot | MET | the final MCP and CLI reports share snapshot `sha256:66d3969c…` (the candidate was `sha256:05008e6f…`) |
| No stale evidence attributed to the repaired code | MET | after the repair, both pre-repair findings return STALE_SNAPSHOT with `superseded_by_run` (recorded files 25-26; `test_findings_are_fetchable_and_marked_stale_after_the_repair`) |
| Unknown required work prevents PASS | MET | `test_unknown_required_work_is_incomplete_not_pass_through_the_service`. In the recorded run, the whole-tree `guard check` is INCOMPLETE because a list-comprehension division is UNKNOWN. |
| Agent never auto-approves a baseline/contract change | MET | no such tool (`test_tool_set_has_no_approval_baseline_or_execution_tools`); authority unchanged by every tool (`test_contract_authority_is_unchanged_by_every_tool`) |
| Complete loop measured separately from verifier latency | MET | loop 50.2 s; model 43.5 s; verifier 6.2 s in the loop (STATUS "M5 repair loop") |
| Hook/CI enforcement where the client supports it | OPEN | not implemented. MCP alone is not enforcement, and the documentation says so. |

## docs/11 "Before an alpha"

| Release criterion | Status | Evidence or gap |
| --- | --- | --- |
| M0-M5 gates | MET (limits) | tables above |
| Working no-model path | MET | `guard check`/`guard verify` never contact a model; the recorded run's step 9; T45 |
| Actual local model path | MET (limits) | M4 evaluation; the recorded run's repairs and discovery use the real model; one model only |
| Supported-subset docs | MET | STATUS "Supported Python subset", M2-M5 additions, "Known gaps" |
| Schema-valid reports | MET | the CLI and the MCP service validate every report before returning it (a violation is ENGINE_ERROR); `detail="full"` reports, findings and evidence are validated in tests |
| Replayable regression | PARTIAL | Each regression is replayed in both snapshots during `verify --execute`, with stored witnesses and environment digests, and re-running verify reproduces it. A standalone `guard replay FINDING_ID --run-profile NAME` (docs/09) is not implemented. |
| Offline operation | MET | the verifier uses no network; the default model path is loopback-only (the I-105 remote proposer is an explicit, disclosed opt-in and is never needed by check, verify or the MCP tools); Docker probes run with `--network none`; `guard runner setup` is the only networked command and is explicit; tests run offline; the MCP server opens no socket |
| One real client repair integration | PARTIAL | The repair loop ran through the real MCP server with the official SDK client, but the agent was a local-model harness. Claude Code's `.mcp.json` is tested only at launch, handshake, `tools/list` and `verify_patch`, not in a live session. |

## Paths that must work offline, with and without a model

| Path | Command | Model | Network | Verified by |
| --- | --- | --- | --- | --- |
| Static check | `guard check PATH` | no | none | test suite; recorded run step 9 |
| Patch verify (static) | `guard verify --base REF` | no | none | test suite |
| Patch verify + contracts | `guard verify --base REF --execute --run-profile NAME` | no | Docker daemon (local); containers `--network none` | Docker tests; recorded run |
| Agent tools | `guard mcp serve --stdio ...` | no | none (stdio) | `test_mcp_server.py` |
| Contract discovery with model | `guard contracts discover --model local [--execute]` | yes (loopback) | 127.0.0.1 only | M4 tests and evaluation; recorded run step 10 |
| Model absent | the same command with the endpoint down or the model missing | n/a | none | T45: INCOMPLETE (exit 2), templates still run |
| Agent-proposed contracts | `guard contracts context SYMBOL`, `guard contracts propose --file F`; MCP `get_context_packet`/`propose_contracts` | no (the calling agent proposes) | none | `test_agent_proposals.py`, `test_agent_proposals_over_mcp` (I-106) |
| Remote proposer (opt-in, **not offline**) | `guard contracts discover --model remote` with `provider = openai_compatible|anthropic`, `remote_allowed = true` | yes (remote) | https to the configured host only; context packets LEAVE the machine (disclosed per packet) | fake-provider tests (`test_remote_adapters.py`, `test_remote_discovery.py`); hosted providers NOT exercised (no key on this host) (I-105) |
| Remote provider down or refused | the same command with a 401, a timeout, a redirect or a misconfigured destination | n/a | none after the failure | INCOMPLETE (exit 2) or exit 4; no fallback to the local model and none from it (`test_remote_failures_are_incomplete_with_no_fallback`) |

## Not in this alpha (by plan or blocker)

- **M6 Kotlin**: DEFERRED. The host has no JDK or Kotlin toolchain and a download was not authorized; the written feasibility decision is I-101.
- **M7**: not started.
- **T72/T73**: no labeled held-out patch benchmark or ablations, so no effectiveness claims.
- **Public release** (naming, licenses, packaging; docs/11 "Before public package release"): not done, and nothing was published.
