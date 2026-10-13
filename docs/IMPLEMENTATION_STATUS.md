# Implementation status

Last updated: 9 October 2026 (paper stage: `benchmarks/run_all.py` executed end to end on engine 0.5.0, `paper/invariantguard.tex` written from its results; see "Paper benchmarks and report"). Earlier on 8 October: (user-requested M4/M5 extension, engine 0.5.0: bring-your-own-key remote proposers and agent-proposed contracts, DECISIONS I-105-I-109; see "Remote and agent proposers"). Earlier the same day: adversarial audit of M0-M5, focused on M4/M5 (see "Audit (M4/M5)"); M5 implemented: stdio MCP server, cancellation, evidence retrieval and a recorded repair loop; M6 deferred; before that: M4 local open-weight contract proposals, M3 and the M2/M3 adversarial audit. This file records only what was actually run in this repository.

## Active milestone

**M5 (agent-native alpha) is at its acceptance gate; M6 (Kotlin) is DEFERRED.** A user-requested M4/M5 extension (engine 0.5.0) adds opt-in bring-your-own-key remote proposers and agent-proposed contracts through the same pipeline ("Remote and agent proposers"; no hosted provider was exercised). M0-M5 tasks (T00-T55) pass their acceptance checks; the evidence is below ("M5" rows, "M5 measurements", "M5 repair loop") and the gate-by-gate map is `docs/ALPHA_CHECKLIST.md`.

- `guard mcp serve --stdio` (official MCP Python SDK `mcp==2.3.0`, optional extra) exposes `verify_patch`, `check_scope`, `get_contracts`, `get_finding`, `explain_finding` and `get_capabilities` over the same orchestration code as the CLI (`service.py`).
- Restrictions: configured roots only; relative scopes that cannot escape; run profiles only when preauthorized at startup, unchanged since and isolated (docker); no approval, baseline, discovery or command tools.
- Cooperative cancellation and server timeouts finalize runs as failed (nothing published). Stale evidence is flagged as STALE_SNAPSHOT, never attributed to newer code.
- The recorded repair loop (`examples/demo_repo/recorded_run/`) used a local-model harness as the agent client (not Claude Code): real unsafe patch, BLOCK with a static witness and an observed regression, model repairs applied in a temporary repository, final snapshot PASS.
- M6: there is no JDK, `kotlinc` or Gradle on this host (`/usr/bin/java` is the macOS stub), and a toolchain download was not authorized. T60 is a written feasibility decision only (DECISIONS I-101); T60-T65 are open.

Previous milestone summary (M4, unchanged): **M4 (local semantic discovery) is at its acceptance gate.** M0-M4 tasks (T00-T04, T10-T17, T20-T28, T30-T38, T40-T45) pass their acceptance checks; the evidence is below ("M4" rows, "M4 measurements"). The local model is qwen2.5-coder:7b served by Ollama on 127.0.0.1. It proposes contract candidates that pass through the same validation, non-vacuity and falsification pipeline as template candidates, and it never sets authority above INFERRED. Only one open-weight model was evaluated: a second one would need a download, which was not authorized (I-089). There is no MCP server or Kotlin adapter (M5, M6).

## Tested host and versions

| Item | Value |
| --- | --- |
| OS / CPU | macOS 15.6 (24G84), Apple M4 (Mac16,12, 10 logical CPUs), 16 GB RAM |
| Interpreter | CPython 3.13.9 (`python3`), project venv `.venv` |
| Parsed grammar | host CPython 3.13 grammar (`ast` + `symtable`) |
| Solver | z3-solver 5.1.0.0 (`z3.get_version_string()` = 5.1.0), optional extra `solver` |
| Git (M2 reads) | git 2.52.0 (`/opt/homebrew/bin/git`); only read plumbing is used (I-037) |
| SQLite (state) | stdlib `sqlite3`, SQLite 3.51.0; state schema v3 (M5: append-only report index, additive migration from v1/v2, I-096; v2 was M3, I-057) |
| Tests / schema | pytest 9.1.1, jsonschema 4.26.0, referencing 0.37.0 (extra `dev`) |
| Runtime dependencies | core: none (stdlib only; the M4 model client and the I-105 remote adapters are stdlib `http.client`/`ssl`; no Anthropic or OpenAI SDK). M5 optional extra `mcp`: `mcp==2.3.0` (official MCP Python SDK, MIT) with 24 transitive packages (anyio 4.15.1, pydantic 2.14.0, starlette 1.7.0, httpx2 2.13.1, ...), all pinned in `requirements.lock`; `pip freeze --exclude-editable` equals the lock (only its header comments differ). The core never imports the SDK at startup (test) |
| Engine identities | engine 0.5.0 (remote and agent proposers, I-108; 0.4.1 from the M4/M5 audit, I-102; 0.4.0 for M5, I-100; 0.3.1 through M4), IR `ir-0.2`, semantics profile `python3.13-cpython-builtin-v4` (M4/M5 audit, I-102; v3 from the M2/M3 audit, I-074), DSL `dsl-1`, input profiles `profiles-1`, probe protocol `ig-probe-1` |
| Docker (M3 runner) | Docker Desktop, server 29.5.2 (linux/aarch64 VM, cgroup v2, runtime runc), CLI `/Applications/Docker.app/Contents/Resources/bin/docker` 29.5.2 |
| Local model runtime (M4) | Ollama 0.35.1 (`ollama --version`: "ollama version is 0.35.1"; `GET /api/version` = 0.35.1), app at `/Applications/Ollama.app`, serving on 127.0.0.1:11434; inference process `llama-server`, 100% GPU (Metal) per `ollama ps` |
| Local model artifact (M4) | `qwen2.5-coder:7b`, digest `sha256:dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364` (from `GET /api/tags`; matches `ollama list` ID dae161e27b0e), GGUF, family qwen2, 7.6B parameters (7,615,616,512), quantization Q4_K_M, 4,683,087,561 bytes, context length 32,768, base model https://huggingface.co/Qwen/Qwen2.5-Coder-7B (finetune "Instruct"). Re-pulled by explicit user-approved setup on 2026-10-08; guard never pulls |
| Model license (M4) | From the artifact itself (`ollama show qwen2.5-coder:7b --license`, 202 lines): Apache License 2.0 ("Copyright 2024 Alibaba Cloud"). The license text from `POST /api/show` (11,343 characters) has sha256 `832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e`, which is what the adapter records. The CLI prints the same text plus a trailing newline (sha256 `c156170b718ec29139d3653d40ed1986fd92fb7e0959b5c71f3c48f62e6636f4`, checked identical after stripping); GGUF metadata `general.license = apache-2.0`, `general.license.link = https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/blob/main/LICENSE`. Not redistributed by this repository |
| MCP (M5) | SDK `mcp` 2.3.0 / `mcp-types` 2.3.0; protocol versions negotiated in tests: 2025-11-25 (initialize handshake, `mode="legacy"`) and 2026-07-28 (`server/discover`); transport stdio only |
| JVM / Kotlin (M6) | none: `/usr/bin/java` prints "Unable to locate a Java Runtime"; `/usr/libexec/java_home -V` finds none; `kotlinc`, `kotlin`, `gradle` not found |
| Runner image | `python:3.13-slim`, image ID = repo digest `sha256:3dd7cc108ec1493442514f5c2a871af6af0ec31d768ff6e378a93340c3b3db5f` (arm64), CPython 3.13.16 inside (minor matches the host 3.13). Prepared with `guard runner setup --run-profile isolated-python` (exit 0, 4.1 s; the first download had been done with `docker pull python:3.13-slim` while checking the daemon, so setup's pull was a no-op) |

Install: `python3 -m venv .venv && .venv/bin/pip install -e '.[solver,dev,mcp]'` (or `-r requirements.lock` then `-e . --no-deps`). Dynamic checks additionally need Docker and `guard runner setup` (or an explicitly unisolated profile).

## Remote and agent proposers (M4/M5 extension, precise)

This extension was requested by the user. Trigger: "the local model is heavy on the developer's machine" (DECISIONS I-105-I-109). The local loopback model stays the fully offline default. The two new sources feed the same parser, validation, non-vacuity, de-duplication, falsification and authority pipeline as Ollama and the templates. No source can produce authority above INFERRED, approval, proof or a baseline change.

**What exists.**
- **Remote adapters** (`semantic/remote.py`, I-105/I-107). They use stdlib `http.client` + `ssl`; no new dependency was added, and `requirements.lock` is unchanged (`pip freeze --exclude-editable` equals it).
  - `OpenAICompatibleReasoner`: `GET {base_url}/models`, then `POST {base_url}/chat/completions` with `response_format` json_schema. With `structured_output = "auto"`, a 400/422 retries once in `json_object` mode; the downgrade is recorded.
  - `AnthropicReasoner`: `GET /v1/models/{id}`, then `POST /v1/messages` with `x-api-key`, `anthropic-version: 2023-06-01` and `output_config.format` json_schema. The schema is a leveled, non-recursive copy with unsupported constraints removed; `auto` downgrades to prompt-only JSON on a 400/422. No sampling parameters and no server-side fallbacks are sent. Text is read from text blocks only, and `stop_reason` (refusal category) is recorded.
  - Both reject a served model that is not the requested id or `<id>-<suffix>` (MODEL_MISMATCH).
- **Configuration** (`[semantic]`): `provider = "loopback" | "openai_compatible" | "anthropic"`, `base_url` (Anthropic default `https://api.anthropic.com`), `model_id` (no default), `api_key_env` (the NAME of the variable; required for Anthropic), `remote_allowed`, `structured_output`, `max_output_tokens` (default 16000), `max_request_bytes` (default 256 KiB) and `effort`.
  - Non-loopback destinations need https with TLS verification always on, plus `remote_allowed = true`.
  - Plain http to a non-loopback host, user info, query, fragment, path traversal, numeric host shorthands and `*.localhost` are refused at config parse (exit 4), at adapter construction and at connect time. A loopback peer is re-checked after connecting.
  - Redirects are rejected, proxies are never consulted, and every request has a timeout and a size cap.
- **Secrets.** The key is read from the environment per request and sent only in one header. It is redacted from provider error text and from response bytes before parsing. It is never in config, logs, reports, evidence, cache keys, SQLite, contract files, artifacts or exception text; tests assert each of these.
- **No fallback.** A failed provider makes discovery UNAVAILABLE and the run INCOMPLETE (exit 2); deterministic discovery continues. `--model local` refuses a non-loopback destination (exit 4, nothing sent). `--model remote` refuses the loopback Ollama provider. Neither direction falls back to the other.
- **Data-egress disclosure.**
  - Per function: `egress` with provider, host, model_id, `context_left_machine`, context packet digest and byte size, request bytes and sha256.
  - Per run: `model.data_egress`, with a warning when the context left the machine that providers (especially free tiers) may retain or train on submitted data.
  - Provenance kind `remote_model` with `egress` travels with the contract into `guard contracts list` and MCP `get_contracts` (`proposer`), into verify report contract results (`proposer`) and into EXECUTION evidence (`contract.proposer`).
  - `guard capabilities --path P` and MCP `get_capabilities` (per root) describe the configured proposer without contacting it.
  - An OpenAI-compatible endpoint on loopback (Ollama `/v1`) counts as `local_model` with `context_left_machine: false`.
- **Agent-proposed contracts** (`semantic/agent.py`, I-106).
  - MCP `get_context_packet(repository, symbol)` is read-only and creates no `.guard` state. It returns the exact ContextPacket the model adapters get, its digest, the per-packet candidate schema, the DSL node list and the protocol text.
  - MCP `propose_contracts(repository, symbol, context_packet_digest, candidates[, run_profile, dry_run])` takes at most 8 candidates and 64 KiB in a closed schema.
  - CLI mirrors: `guard contracts context SYMBOL --format json` and `guard contracts propose --file F [--client NAME/VERSION] [--execute --run-profile NAME] [--dry-run]`. A file holds at most 16 batches.
  - Batches are parsed with the model-output parser against a freshly built packet. A different digest is STALE_CONTEXT. Header fields beyond the four allowed reject the whole batch (FORBIDDEN_FIELD). JSON depth over 40 or size over 64 KiB rejects the batch.
  - Accepted candidates are recorded INFERRED with provenance `{kind: "agent_proposed", client: {name, version}}`. The client comes from MCP `clientInfo` or `--client`; it is self-reported and disclosed as such.
  - Through MCP, falsification runs only with a preauthorized, unchanged, isolated (docker) profile. Each falsified candidate gets a content-addressed `discovery_falsification` artifact, returned as its evidence id.
  - There is still no approve, revise, reject or baseline tool.
- **Schemas** (I-108, additive within 1.0): `data_egress`, `proposer_client`, `proposer`; provenance kinds `remote_model`/`agent_proposed` with optional `client`/`egress`; CandidateBatch sources plus optional `data_egress`/`proposer_client`; report contract result `proposer`; new `agent_candidate_batch.schema.json`. Engine 0.5.0; IR, DSL, profiles and semantics profile unchanged.
- **Agent-proposer evaluation input** (`benchmarks/agent_eval/`, I-109). It contains `packets/<slug>.json` for all 13 M4 held-out functions (exact `guard contracts context` output), `index.json`, the batch schema, a README with the protocol, `make_packets.py` and `score.py`. `score.py` runs a proposals directory through the `propose` path in one dry run with templates and Docker falsification, then scores it with the M4 scorer. Proposals were later written by three blinded Claude Sonnet 5.5 subagents and scored in the paper stage ("Paper benchmarks and report").

**Not done / not exercised (stated, not claimed).**
- **No hosted provider was contacted.** There is no hosted-provider API key on this machine. The OpenAI-compatible and Anthropic adapters are unit- and integration-tested against a fake provider on 127.0.0.1, including a simulated non-loopback destination; TLS refusal is tested with a self-signed local server. The OpenAI-compatible adapter was run for real only against Ollama's loopback `/v1` endpoint (qwen2.5-coder:7b; see "Validation commands"). Request shapes for hosted APIs follow the vendor documentation and are unverified against live services.
- The agent-proposer evaluation has results for one model family only (three runs; "Paper benchmarks and report").
- `propose_probe_parameters` and `explain_finding` remain UNSUPPORTED for every adapter.
- `max_context_bytes`/`max_context_tokens` bound the packet, not the provider's tokenizer. The token figure is still the ceil(bytes/3) estimate.

## M5 additions (precise)

**What exists.** An agent-facing layer over the existing CLI orchestration, served over MCP stdio.

- **`service.py`** (no MCP import; I-092). `AgentService.call(tool, args)` validates arguments against closed schemas, dispatches to the same functions as the CLI (`run_verify`, `run_check`, `contracts.commands.cmd_list`) and returns `{schema_version: "1.0", tool, status, ...}`.
  - Status values: OK, STALE_SNAPSHOT, TIMEOUT, CANCELLED, INVALID_REQUEST, NOT_AUTHORIZED, NOT_FOUND, ENGINE_ERROR.
  - `verify_patch`/`check_scope` return the run ID, report digest, base and candidate snapshots, verdict, CLI-equivalent exit code, compact coverage and findings, compact contract results, verdict reasons, errors, capabilities, timings, configuration state and the last log lines. `detail="full"` adds the schema-valid report. A report that fails `report.schema.json` is ENGINE_ERROR.
  - `get_finding` returns the finding, its obligation result or contract result, its evidence records, the checked contract revision (precondition, predicate, profile, examples; semantic digest compared with the evidence), the run identity and freshness. `explain_finding` adds a deterministic explanation (I-097).
- **`mcp_server.py`** (I-091). SDK lowlevel `Server` with six tools, explicit input schemas, an output schema, annotations (read-only hints), `structuredContent` plus a JSON text block, and `isError` for error statuses.
  - stdout carries frames only: the SDK diverts fd 1, `sys.stdout` is rebound to stderr, and buffers are flushed before restore. Logs go to stderr. OpenTelemetry middleware is removed.
- **Restrictions** (T51, I-093):
  - roots fixed at startup (`--root [ALIAS=]PATH`), addressed by alias or exact path;
  - `path` is a relative scope (`..`, absolute and symlink escapes rejected);
  - configuration only from `<root>/.guard/config.toml`;
  - `--allow-profile NAME` pins the profile's resolved definition digest at startup, and only docker profiles qualify;
  - option-like or whitespace bases are rejected;
  - tools never write outside `<root>/.guard`, never change contracts or baselines, never execute commands.
- **Cancellation** (T52, `cancellation.py`, I-094):
  - a token is bound per worker thread, with safe points after capture and lowering, per symbol, before the consistency check, and at every runner process start and wait (process group killed);
  - `Cancelled` is a `BaseException`;
  - a cancelled verify run is finalized `failed` with note `cancelled: REASON`: no cache entries, contract evidence or report;
  - client `notifications/cancelled` interrupts the handler (no response, per protocol);
  - server timeout (`--timeout-s`, per-call `timeout_s`) gives an explicit TIMEOUT result.
- **Evidence index** (T52, I-096): state schema v3 `run_reports` + `report_findings` (append-only; the report body is a content-addressed artifact). Every finished verify (CLI or MCP) is indexed. `check_scope` reports live in the server session. Freshness re-captures the working tree and compares snapshot IDs (STALE_SNAPSHOT, `superseded_by_run`).
- **CLI**: `guard mcp serve --stdio --root [ALIAS=]PATH ... [--allow-profile NAME ...] [--timeout-s S]`; `guard explain FINDING_ID [--run RUN_ID] [--format json|human]` (docs/09); `guard capabilities` reports MCP "available (explicit)" when the SDK is installed, otherwise "unavailable".
- **Client integration** (T53, I-098): `docs/MCP_INTEGRATION.md` holds a project-scope Claude Code `.mcp.json` example and an agent convention. The test launches exactly that JSON's command. It is not installed into any user configuration, and a live Claude Code session was not driven.
- **Repair loop** (T54, I-099): `examples/demo_repo/run_demo.py` and the write-once `recorded_run/` (see "M5 repair loop").

**Not implemented (documented, not claimed).**
- `guard replay`.
- Enforcement hooks or CI gating: MCP is a convention, not enforcement.
- Model discovery as an MCP tool (deliberately not exposed).
- Cancellation inside a single function's lowering or interval analysis: the safe points are between stages and symbols, and the per-query Z3 timeout bounds solver time.
- Streamable HTTP or SSE transports.
- A Claude Code session test.

## M4 additions (precise)

**What exists.** `guard contracts discover --model local` asks a configured local open-weight model for contract candidates. Its answers enter the same mechanical pipeline as deterministic template candidates. Every validated candidate from either source can be falsified dynamically (`--execute --run-profile NAME`). The model is a proposer only: it cannot set authority (everything is INFERRED), approve, record evidence or produce proof.

Pipeline, per target function (`semantic/discovery.py`, `contracts/commands.cmd_discover`):

1. **Targets.** The same targets as templates (`templates.target_functions`): module-level plain functions with one input profile established from annotations or doctest literal types.
2. **ContextPacket** (`semantic/context.py`, AST only, nothing imported).
   - Contents in priority order: the target function in full, then direct callees in the snapshot, direct callers (same module or `from M import f`), then tests that mention the target.
   - Budget: `max_context_bytes` (12,000) and `max_context_tokens` (4,000, estimated as ceil(bytes/3)). Items that do not fit are omitted and the omission is stated. A target that does not fit is skipped (`CONTEXT_TOO_LARGE`), never truncated.
   - Trusted static facts: signature, annotations labelled "NOT enforced", the input profile, and syntactic unsupported constructs.
   - The packet is schema-valid (`context_packet.schema.json`), every snippet is `trusted: false`, and the packet digest is recorded.
3. **Prompt** (`semantic/prompt.py`, version `ig-contract-prompt-1`).
   - A fixed system message holds the DSL reference, the rules and one worked example (`halve_all`, excluded from evaluation).
   - The user message frames each snippet between `<<<UNTRUSTED-nonce>>>` markers. The nonce comes from the packet digest and is re-derived if it occurs in the source.
   - The structured-output JSON Schema (Ollama `format`) restricts syntax: recursive precondition/predicate nodes, parameter-name enum, profile const, at most 3 candidates.
4. **Adapter** (`semantic/ollama.py` over `semantic/endpoint.py`). See T41 and I-079 for the loopback rules.
   - Identity is read from the runtime: digest (`/api/tags`), quantization and context length (`/api/show`), declared license id plus the sha256 of the shipped license text, and runtime version (`/api/version`).
   - A pinned `model_digest` refuses any other artifact.
5. **Strict parsing** (`semantic/candidates.py`): whole-answer and per-candidate rejection codes (I-082). Accepted candidates are built by the engine as INFERRED/VALIDATED with `local_model` provenance (artifact digest, prompt version, packet digest). Boundary inputs are validated plain values that become `examples`.
6. **Shared pipeline** (`templates.validate_proposals`, both sources).
   - `validate_revision`: schema, symbols, typing, budgets, behavior reference, admissible precondition.
   - The behavioral non-vacuity filter (`contracts/vacuity.py`, I-083).
   - Then de-duplication by meaning (I-085). Templates come first, so a model restatement of a template is `duplicate`.
7. **Falsification** (`contracts/falsify.py`, optional, both sources). Probes run in the configured runner on the current snapshot, with domain-preserving shrinking and a fresh-run confirmation (I-084).
   - REFUTED (OBSERVED_FAILURE, EXECUTED) candidates are reported and not recorded.
   - TESTED means finite probes only.
8. **Recording.** Unless `--dry-run`, surviving candidates are recorded with `ContractRegistry.create(kind="propose")`: INFERRED, no evidence. Approval stays `guard contracts approve`. If the working tree changed during discovery, nothing is recorded (INCOMPLETE).

Commands and configuration:
- `guard contracts discover [--model local] [--execute --run-profile NAME] [--symbol SYM]... [--max-functions N] [--show-context] [--dry-run]`.
  - Exit 0, or 2 (INCOMPLETE) when a requested capability (model, runner) was unavailable or the tree changed.
  - Without `[semantic] enabled` it is exit 4 and nothing is contacted.
- `guard model status` reports identity, license, runtime and structured-output support. It uses read-only requests to the configured endpoint (exit 0, 2 if unavailable, 4 if not configured).
- `guard capabilities` reports the model as "available (explicit)" without contacting anything.
- `[semantic]` keys and defaults are in I-088. `[privacy] store_raw_model_context` (default false) controls raw packet/prompt/response retention (I-087).

**Not implemented (documented, not claimed).**
- `propose_probe_parameters` and `explain_finding` return UNSUPPORTED (I-078).
- No second open-weight model was evaluated: that would need a download, which is not authorized (I-089).
- No embedded runtime, and no remote or OpenAI-compatible endpoint.
- Falsification results from discovery are not stored as evidence.
- Context selection is syntactic: direct calls by name, `from`-imports, and tests that mention the target name.

## M3 additions (precise)

**What exists.** Behavioral contracts over module-level Python functions, checked dynamically in an isolated runner on the *same* inputs in the base and candidate snapshots:

- a typed JSON property DSL (`contracts/dsl.py`) with a validator and a pure evaluator;
- bounded input profiles with deterministic generation and domain-preserving shrinking (`contracts/profiles.py`);
- contract revisions, authority events and evidence, stored as immutable JSON files plus append-only SQLite rows (`contracts/registry.py`);
- deterministic template discovery (`contracts/templates.py`);
- run profiles, a Docker backend whose isolation claims are checked from inside the container, and an explicit unisolated backend (`execution/`);
- a trusted probe harness (`execution/probe_harness.py`, shipped as data, never imported by the engine);
- differential replay, shrinking, fresh-run confirmation and the contract gate policy (`contracts/checker.py`);
- `guard verify --execute --run-profile NAME`, `guard contracts list|show|declare|approve|reject|retire|revise|import|discover`, `guard runner setup|probe`.

**DSL v1 (docs/06 node set).** References `arg`, `result`, `before_arg`, `after_arg`; scalars `literal` (int/float finite with |v| <= 2^53, bool, null; no strings), `eq`, `lt`, `le`, `gt` (canonicalized to `lt` with swapped operands), `and`, `or` (1..32 operands), `not`; built-ins `len`, `sum`, `all_finite`, `approx_eq` (atol, rtol and an explicit `require_finite` NaN/infinity policy are mandatory), `all_between` (inclusive, `lower <= upper`); relations `unchanged_argument(name)` and `repeatability(repeats 2..50, seed, comparison="exact")`. Anything else, extra fields, non-object nodes, unknown parameter names, more than 64 nodes, depth over 16, files over 32 KiB and non-finite JSON constants are rejected with a code (`UNKNOWN_NODE`, `MALFORMED_NODE`, `TYPE_ERROR`, `BUDGET_EXCEEDED`, `UNKNOWN_SYMBOL`, `UNSUPPORTED_TARGET`, `VACUOUS`, `CONTRADICTORY_DOMAIN`, `INVALID_CONTRACT`, `MISSING_DOMAIN`, `MISSING_PROVENANCE`, `UNKNOWN_PROFILE`, `OVERSIZED`, ...). Semantics are listed in `contracts/dsl.py` and I-054.

**Validation (docs/06 "Candidate validation").** Schema version and fields; a known target (AST only: a module-level, undecorated, non-async `def` without `*args`/`**kwargs` in an importable dotted module); every parameter covered by the input profile; DSL typing and budgets; a nonvacuous predicate (it must reference `result`, `after_arg`, `unchanged_argument` or `repeatability`); the precondition may only constrain inputs; examples must lie in the profile; and an admissible witness: a deterministic bounded search (examples, boundary inputs, seeded random inputs; at most 256) must find an input satisfying profile and precondition, otherwise `INVALID_CONTRACT` (never evidence that the contract holds).

**Input profiles.** `bounded_builtin_numeric_list_v1` (nonempty list of 1..16 exact built-in int/float, bool excluded, finite, within ±1e6), `bounded_builtin_int_list_v1` (0..16 ints within ±1000), `bounded_builtin_ints_v1`, `bounded_builtin_finite_floats_v1`. A profile applies to every parameter of the target. Simple precondition conjuncts (`all_between`, comparisons of `len(arg)` or a scalar `arg` with a literal) steer generation; admission always re-checks the full profile and precondition.

**Lifecycle and authority.** Revisions are `.guard/contracts/<id>/rNNNN.json`, authority events `.guard/contracts/<id>/events/eNNNN.json`, both created exclusively and indexed in `state.sqlite` with their sha256 digests; SQLite triggers abort UPDATE/DELETE on revisions, events and contract evidence. Events: `propose` (template -> INFERRED), `declare` (maintainer -> DECLARED), `approve` (INFERRED/REJECTED -> APPROVED), `reject` (INFERRED -> REJECTED), `revise` (new revision; APPROVED/DECLARED keep their authority, INFERRED stays INFERRED), `retire`, `import`. Lifecycle state is derived: VALIDATED at creation, CHECKED once evidence exists for the current revision, REVIEWED after a maintainer decision. An indexed file whose bytes changed is an integrity error (the contract is unusable; a required one makes the run INCOMPLETE). Files that were never recorded through `guard contracts` are ignored until `guard contracts import --reason`.

**Evidence binding (T38).** Evidence binds to the revision's semantic digest (scope, canonical precondition and predicate, profile, examples, DSL and profile versions). A new predicate, tolerance, domain, profile or target is a new revision with no evidence; old evidence stays in the store as history. Each symbol's static validity key carries the digest of the active contract revisions scoped to it, so declaring, approving, revising or retiring a contract re-keys exactly that symbol's summary (T23 contract dependency).

**Execution boundary.** Nothing runs unless `guard verify` gets `--execute` and the run profile is defined in `[execution.profiles.NAME]` with `[execution] enabled = true` (the configuration is the reusable authorization). Static `check`/`verify` never start a runner (tested by monkeypatching the runner to fail). Backends:

- *docker*: per batch a fresh container: `--network none --read-only`, the snapshot copy and the harness bind-mounted read-only, `--tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m`, `--user 65534:65534 --cap-drop ALL --security-opt no-new-privileges`, `--pids-limit/--memory/--memory-swap/--cpus`, `--ulimit cpu=N`, `--ipc none --log-driver none`, only `HOME=/tmp LANG=C.UTF-8 PYTHONHASHSEED=0` plus the profile's explicit env allowlist, `--pull never`, and the image by its local ID. The docker CLI itself gets a minimal environment. Before any target import, a capability probe container started with the same flags checks from the inside: no interface up and no route (and a TEST-NET connect fails with ENETUNREACH); the source mount is read-only; writes outside the scratch tmpfs fail and no writable non-virtual mount exists; no unexpected environment keys, no credential paths, uid != 0, effective capabilities 0; cgroup memory/pids/cpu and RLIMIT_CPU equal the profile; the interpreter minor equals the host's. A failed or unprobed required capability, a missing CLI or daemon, or an image that was not prepared gives RUNNER_UNAVAILABLE: required contract checks are UNKNOWN and the run is INCOMPLETE. There is never a fallback to unisolated execution.
- *unisolated*: only when the profile says so and `[execution] allow_unisolated = true`. The harness runs as a local `python -I -S -B` subprocess with a minimal environment in a temporary copy; all isolation capabilities are reported NOT_ENFORCED, and reports and evidence say UNISOLATED.

`guard runner setup` is the only command that uses the network (`docker pull` of the configured image). `guard runner probe` runs the capability probe only.

**Probe harness.** The parent process never imports target code. Each probe runs in a forked child in its own session. The child imports the module from the copied snapshot, decodes fresh copies of the inputs, snapshots the arguments, calls the allowlisted function, encodes the result or exception, and sends one length-prefixed frame. The parent enforces the per-probe wall limit (SIGKILL to the child's process group), the CPU limit and the result size limit. Values cross the boundary in an exact tagged encoding of built-in types only (`contracts/values.py`); anything else is opaque (UNKNOWN). Encoding and decoding never call a method defined by the target.

**Differential check (T35).** For every active contract (APPROVED, DECLARED, INFERRED):

1. Admit up to `max_probes` (default 48) inputs; inputs outside the profile or precondition are excluded and counted, never run.
2. Run the same inputs in base and candidate (two batches in parallel).
3. Evaluate the predicate on the host.
4. Classify each input: base PASS + candidate FAIL = regression; both FAIL = pre-existing; base FAIL + candidate PASS = resolved; a candidate failure without a usable base = undetermined; import errors, timeouts, crashes, output limits and opaque values are UNKNOWN (runner or environment outcomes, never defects).
5. Shrink the first counterexample (strictly simpler candidates that satisfy profile and precondition and keep the same classification; at most 12 rounds of 24 candidates).
6. Confirm the original and minimized witnesses in fresh runs: base once, candidate twice.

Result labels:

- OBSERVED_REGRESSION (scope EXECUTED, relation INTRODUCED) only for a confirmed base pass and candidate failure on the same witness in comparable environments.
- If the dependency lock files differ, or the base cannot run the witness, the result is OBSERVED_FAILURE with relation UNDETERMINED.
- Both failing gives OBSERVED_FAILURE PRE_EXISTING.
- Passing probes give TESTED (EXECUTED, quantifier "for the N executed admitted input(s) only (finite probes; not a proof)").

**Gate policy (T37, docs/03 rows).**

- A replayed INTRODUCED regression of an APPROVED or DECLARED contract is ERROR, so the verdict is BLOCK ("blocking contract regression").
- Any change to an INFERRED contract is WARNING ("inferred change warns").
- PRE_EXISTING and UNDETERMINED failures are WARNING.
- A base-only violation that the candidate fixes is a RESOLVED INFO finding.
- A required contract check without evidence (runner unavailable, integrity error, target unavailable, not allowlisted, all probes undecided, or a required violation that cannot be attributed) makes the run INCOMPLETE. "Required" means APPROVED or DECLARED, fixed in `analysis_plan.required_contracts` before analysis.
- With `analysis.require_coverage = false` those conditions WARN instead.
- REJECTED and RETIRED contracts have no policy effect.
- Without `--execute`, active contracts are listed as NOT_RUN / UNVERIFIED and do not change the verdict.

**Templates (T32).** `guard contracts discover [--dry-run]` reads source only and proposes INFERRED candidates:

- `purity` (`unchanged_argument` for a list parameter with no syntactic mutation, aliasing or passing to non-allowlisted calls);
- `length` (`len(result) == len(p)` for filter-free list comprehensions over `p`);
- `assertion` (entry `assert`s become the precondition; an `assert` on the returned name becomes a predicate);
- `repeatability` (no `random`/`time`/`datetime`/`uuid`/`os`/`secrets` names, no `global`/`nonlocal`).

The input profile comes from annotations or from the literal argument types of doctest calls (`ast.literal_eval`, never executed); doctest inputs become examples. Every candidate keeps provenance (template, docstring, assertion), its source span, its assumptions and a schema-valid CandidateBatch. It is validated before it is recorded, and nothing is ever approved by discovery.

**Report additions (schema 1.0, additive, I-069).**

- `contracts` section: the execution backend, its status, capabilities and environment, the RunProfile document and its digest, the registry state, and per-contract results.
- `timing_ms.dynamic` and `timing_ms.runner_probe`.
- `capabilities.execution` is one of `disabled`, `unavailable`, `isolated`, `unisolated`.
- EXECUTION evidence records include the run profile digest, environment identity, interpreter, isolation capabilities, typed inputs (or digests when witness storage is off), observations and predicate verdicts.
- Contract findings carry `contract` (id, revision, semantic digest) and `comparison.match = "contract_scope"`.
- `analysis_plan.required_contracts`.

## M2 additions to the supported subset (precise)

These extend the M1 subset below. Where the two differ, this section wins: M1 said all calls and globals were unknown.

**Properties.**
- `division.zero_denominator` (M1).
- `index.out_of_range`: every subscript whose receiver is an established exact built-in `list` or `tuple`. The index must be an exact int or bool; Python semantics `-n <= i < n`. Slices, dicts and other receivers are not obligations.
- `none.attribute_access`: every attribute load whose receiver may be `None` (kind NONE or OPTIONAL, or narrowed from OPTIONAL) and whose attribute NoneType lacks.
- Inside an instantiated callee each check is also a `*.via_call` obligation of the caller. These are advisory reachable-call evidence (I-034).

**Call summaries (I-031).** A call `f(...)` whose name resolves (I-032) to an undecorated, supported module-level `def` is instantiated at the call site. This works within the module and through `from M import f` when M maps to exactly one captured file (CONDITIONAL). Supported binding: positional and keyword arguments, and literal defaults. These remain unknown calls: recursion, depth > 6, more than 6,000 IR values, `*args`/`**kwargs`, unpacking, mismatched arguments (TypeError is not modeled), decorated, rebound or conditionally bound callees, and modules that use `globals`/`vars`/`setattr`/`exec`/`eval`/`sys.modules`/`__dict__` or star imports.

**Module constants (I-033).** `NAME = <literal>` bound once at module level. Literal means int, bool, float, str, None, a negative number, or a tuple of these.

**Sequences (I-035).**
- Supported: list/tuple displays without `*`, builtin `len` (unless rebound) on exact sequences, and list/tuple truthiness.
- Parameters are exact sequences when declared (`python_exact_builtin_list`, `python_exact_builtin_tuple`) or annotated `list`, `list[...]`, `tuple`, `tuple[...]` (CONDITIONAL).
- After any unknown effect a list's length is unknown, so its index obligations are UNKNOWN. Tuples keep their length.
- Element values are not modeled.

**None-flow (I-036).**
- OPTIONAL values come from `X | None`, `None | X` and `Optional[X]` annotations (with `from typing import Optional`), from the declared profile `python_optional_exact_builtin_int`, and from joins of None with one exact kind (for example a callee that returns None on some paths).
- `x is None` and `x is not None`, including negated forms, narrow `x` in the guarded branch.
- Truthiness of optionals is not modeled.

**Domains (config).** Profiles: `python_exact_builtin_ints`, `python_exact_builtin_list`, `python_exact_builtin_tuple`, `python_optional_exact_builtin_int`. Several `[[domains]]` entries may name one symbol if their explicit `params` are disjoint.

**Patch verification.**
- Commands: `guard verify [PATH] --base REF|git:REF|baseline:LABEL --mode quick|full --format json|human [--no-cache] [--state-dir DIR]` and `guard baseline create|list|show`.
- New config key: `analysis.full_budget_ms` (default 60000).
- Exit codes are the same as `check`.

## Supported Python subset (M1 base, precise)

This is the M1 text, kept as written. Where it says calls, globals or subscripts are unknown, the M2 section above takes precedence. Plain methods (I-130) and `str literal % tuple literal` formatting (I-131) are now supported; see "Subset widening: methods and printf formatting".

**Analyzed symbols:** module-level `def` functions only, without decorators, type parameters, `yield`/`await`, `global`/`nonlocal`, assignment expressions (`:=`), or nested functions that use `nonlocal`. Every other function-like scope (methods, nested functions, lambdas, async functions, functions defined under `if`/`try` at module level), class bodies and module-level code is an **unsupported symbol**. Its `/`, `//` and `%` sites are reported as UNKNOWN obligations.

**Parameters:** positional, positional-only and keyword-only. A parameter is an exact built-in `int` when `.guard/config.toml` declares it (`profile = "python_exact_builtin_ints"`) or when it is annotated `int`. Annotation-only typing is conditional. `bool`/`float` annotations give bool/float kinds. Other or missing annotations, `*args` and `**kwargs` are values of unknown type.

**Statements lowered exactly:** assignment to one or more plain names; annotated assignment to a name; augmented assignment `+= -= *= /= //= %=` to a name; `if`/`elif`/`else` with early `return`; `return`; `raise` (path ends); `pass`; expression statements; docstrings; `assert`, lowered as `if __debug__: if not test: raise AssertionError` with `__debug__` as an explicit runtime-mode input. Code after `return`/`raise` is lowered into a block with no predecessors, so its obligations are counted and proved unreachable.

**Expressions lowered exactly:** literals `int`, `bool`, `float`, `str`, `None`; local names; `+ - *` on exact int/bool (unbounded `python.bigint`); `/` (`python.true_division`, float result, not value-modeled); `//` and `%` (Python floor semantics, encoded exactly in SMT); unary `-`/`+` on int/bool; single comparisons `== != < <= > >=`; `is None`/`is not None`. In branch conditions also: `and`/`or`/`not` short-circuit, chained comparisons, and truthiness of int/bool/float/str/None.

**Lowered as unknown (UNKNOWN_EFFECT, CALL, ATTRIBUTE, INDEX):** all calls, including builtins such as `isinstance`/`len`/`abs` (names can be shadowed; summaries are M2); attribute access; subscripts and slices; global/builtin name reads; reads of unbound locals; `**`, bit operators, `@`; f-strings, conditional expressions, lambdas, comprehensions, container displays, `await`; operators on operands of unknown type; and the statements `for`, `while`, `try`, `with`, `match`, `del`, imports inside functions, nested `def`/`class`, and tuple/attribute/subscript assignment targets. Names an unsupported statement may bind become unknown values afterwards, and any division inside an unsupported construct is an UNKNOWN obligation with that construct as its reason.

**Property:** `division.zero_denominator` for every syntactic `/`, `//`, `%` (BinOp or AugAssign). `divmod()` and other calls are not counted as division obligations. Other exceptions (for example `OverflowError` from huge-int true division) are outside the property. Float values, NaN and infinities are not modeled: a computed float divisor is UNKNOWN, and only a nonzero float literal divisor is proved safe.

**Maybe-unbound locals:** a variable bound on only some paths into a join gets a PHI marked `maybe_unbound`. Every read of it is a checked `ASSIGN` that raises UnboundLocalError in the IR evaluator exactly where CPython does (at the read, including plain aliases such as `x = d`), and the solver prefix requires it to be bound (I-026).

**Source files:** UTF-8 only (a BOM is allowed and excluded from line-1 columns). Files with another declared encoding, or syntax errors, are skipped as `parse_failure`, which makes the run INCOMPLETE. In-scope `.py` files that cannot be captured are required `excluded_path` skipped checks, which also make the run INCOMPLETE (I-027). These are symlinks, files over 5 MiB, unreadable files, and paths containing `:`, control characters, leading whitespace or non-UTF-8 bytes. So is an explicitly targeted non-`.py` file.

## Module boundaries

| Module | Responsibility |
| --- | --- |
| `invariantguard/cli.py` | argparse CLI: `check`, `verify` (M3: `--execute --run-profile`), `baseline create/list/show`, `contracts list/show/declare/approve/reject/retire/revise/import/discover` (M3; M4: `--model local --execute --symbol --max-functions --show-context`), `runner setup/probe` (M3), `model status` (M4), `capabilities`, `init`. stdout carries only the requested output; every report is schema-validated before printing. Exit codes 0-4. |
| `orchestration.py` | Shared stages: `lower_snapshot` (parse + lower + program index), `analyze_lowered` (per-symbol analysis, optional cache), `make_plan` (required obligations fixed before analysis), `build_report`. Runs `guard check` with restart-once. |
| `verify.py` | `guard verify`: base resolution (Git commit or saved baseline), candidate capture with restart-once, impact, base-then-candidate analysis, attribution, patch report; M3: execution setup (capability probe before any import), contract checks, evidence recorded after the consistency check. Also `guard baseline create/list`. |
| `config.py` | strict `.guard/config.toml` parsing; unknown keys are errors; `analysis_key()` (result-affecting configuration). |
| `snapshots/capture.py` | Immutable content-addressed snapshots: working tree (Git-aware listing), Git revision, retained-source baseline. Dependency-lock digests. Re-hash + re-list consistency check. |
| `snapshots/git.py` | Read-only Git plumbing with argument arrays and hardened config (I-037). Status labels (staged/unstaged/untracked/deleted). The only engine module allowed to use `subprocess`. |
| `frontends/python/` | `parse.py`; `symbols.py`; `subset.py`; `resolve.py` (module bindings, global/import resolution, AST fingerprints, dependency records); `lower.py` (typed CFG/IR, frames for instantiated callees, sequences, None narrowing, obligation sites with structural anchors) |
| `ir/` | `model.py` (vocabulary + BUILD_SEQ, kinds list/tuple/optional, `ObligationSite`), `printer.py`, `evaluator.py` (concrete evaluator: sequences, IndexError/AttributeError failures) |
| `analysis/` | `intervals.py`, `absint.py` (+ sequence lengths, havoc at unknown effects), `obligations.py` (three properties, scope/assumption tracking, witness search incl. lists/None) |
| `solver/z3_backend.py` | Optional Z3 adapter: per-function `Context` (I-041), sequence lengths, None-state, optional value parts, timeouts/rlimit, query digests |
| `impact/` | `keys.py` (validity keys), `graph.py` (file diff, symbol matching with certain/uncertain renames, dependency graphs, affected slice + key cross-check), `attribution.py` (witness-in-base classification, RESOLVED) |
| `storage/` | `store.py` (SQLite metadata, content-addressed artifacts, retained source, two-phase cache entries, append-only baselines), `cache.py` (per-symbol cache adapter, binding checks) |
| `policy/verdict.py` | `default-v1`: `severity_for` (check, I-009) and `severity_for_patch` (verify, I-044), precedence ERROR > BLOCK > INCOMPLETE > WARN > PASS, exit codes |
| `models/`, `schemas/` | enums, spans/digests, JSON Schemas (report 1.0 with additive M2 fields, I-048, and M3 fields, I-069) |
| `contracts/dsl.py` (M3) | DSL v1 node set, typing, budgets, `gt` canonicalization, pure evaluator (built-in values only; Kleene logic; dyn type mismatch = FALSE; opaque = UNKNOWN) |
| `contracts/values.py` (M3) | exact tagged encoding of built-in values across the runner boundary; defensive decoding; opaque markers |
| `contracts/profiles.py` (M3) | bounded input profiles, precondition hints, deterministic admission (examples, boundary, seeded random), shrink candidates and the simplicity order |
| `contracts/targets.py`, `contracts/model.py` (M3) | AST-only target resolution; revision document checks, semantic digest, full validation incl. vacuity and the admissible-witness search |
| `contracts/registry.py` (M3) | immutable JSON revisions and authority events + SQLite index with file digests; integrity checks; import; evidence rows; per-symbol contract digests for validity keys |
| `contracts/templates.py` (M3) | deterministic templates (purity, length, assertion, repeatability) with provenance, assumptions and a CandidateBatch |
| `contracts/checker.py` (M3) | differential replay, classification, shrinking, fresh-run confirmation, EXECUTION evidence, contract severities and policy inputs |
| `contracts/commands.py` (M3) | `guard contracts ...` and `guard runner ...` |
| `execution/profiles.py` (M3) | `[execution.profiles.NAME]` parsing, RunProfile document and digest |
| `execution/runner.py` (M3) | Docker backend (flags, capability probe from inside, `--pull never`, container removal on timeout), unisolated backend, host workspaces |
| `execution/process.py` (M3) | the only execution-side `subprocess` use: argument arrays, explicit env, new session, bounded output, process-group kill, cancellation cleanup |
| `execution/probe_harness.py` (M3) | trusted harness shipped as data and run only inside the boundary: identity, capability checks, fork-per-probe calls, framed observations |
| `semantic/endpoint.py` (M4) | the only network module: loopback destination validation (IPv4/IPv6), `http.client` to the validated IP, peer re-check, redirect rejection, request allowlist, size limits |
| `semantic/reasoner.py` (M4) | `LocalReasoner` protocol, `ModelIdentity`, capabilities, response and prompt types; UNSUPPORTED results for unimplemented operations |
| `semantic/ollama.py` (M4) | Ollama adapter: identity from `/api/version`, `/api/tags`, `/api/show`; digest pin; `/api/chat` with `format` = JSON Schema, `stream: false`, fixed options |
| `semantic/context.py` (M4) | AST-only ContextPacket builder (target, callees, callers, tests; byte/token budget; omissions; unsupported constructs) |
| `semantic/prompt.py` (M4) | fixed system prompt (`ig-contract-prompt-1`), untrusted framing with nonce markers, per-packet response schema |
| `semantic/candidates.py` (M4) | strict parsing of model output into proposals (whole-answer and per-candidate rejection codes; INFERRED docs built by the engine) |
| `semantic/discovery.py` (M4) | per-function orchestration, failure handling (stop after 2 consecutive transport failures), optional raw-context retention |
| `contracts/vacuity.py` (M4) | behavioral non-vacuity filter shared by template and model candidates |
| `contracts/falsify.py` (M4) | single-snapshot dynamic falsification of candidates (admission, runner, shrinking, fresh-run confirmation) |
| `cancellation.py` (M5) | `CancelToken`, thread binding, `check()` safe points; `Cancelled` (BaseException) |
| `service.py` (M5) | agent-facing tools over the CLI orchestration: argument validation, root/path/profile restrictions, compact responses, finding lookup, freshness, contract definition, deterministic explanation |
| `mcp_server.py` (M5) | MCP SDK adapter (lowlevel `Server`, stdio), worker threads with cancel tokens, timeouts, stdout hygiene; imported only by `guard mcp serve` |

M2 decision procedure per obligation (`analysis/obligations.py`), in order:
1. An unsupported region gives UNKNOWN.
2. An unreachable site gives PROVED_SAFE.
3. Operand types that are not exact built-ins give UNKNOWN. A list that may have been mutated by an unknown effect also gives UNKNOWN.
4. Cheap facts give PROVED_SAFE when they exclude the failure: the divisor excludes 0, the index lies in `[-len, len)` for every possible length, or the receiver was narrowed (PATH_FACT).
5. Otherwise the solver checks `reach AND bad-state` (UNSAT gives PROVED_SAFE). A SAT witness is re-validated by the IR evaluator (PROVED_UNSAFE), or the bounded search runs.
6. Anything still undecided is UNKNOWN.

Scope:
- Declared or literal-only domains give EXACT_MODEL. Annotations give CONDITIONAL.
- Resolution assumptions are stated. Cross-module resolution, and unknown effects before a proof, give CONDITIONAL.

## Completed tasks and evidence

### M0 and M1 (unchanged)

| Task | Acceptance check | Evidence |
| --- | --- | --- |
| T00 | clean local install and CLI help | Fresh venv at the scratchpad path: `pip install <repo>` installed only `invariantguard 0.1.0` (no runtime deps). `guard --version` printed `invariantguard 0.1.0`. `guard --help` lists check/capabilities/init. Without z3 the clean env ran `guard check examples/python_demo/candidate` and got `PROVED_UNSAFE [EXACT_MODEL] ... (ratio, WITNESS_SEARCH)`, verdict BLOCK, exit 1, plus `capability unavailable: solver: z3 unavailable (ModuleNotFoundError: No module named 'z3')`. |
| T01 | valid and invalid payload tests; assumptions/coverage required | `tests/unit/test_schemas.py` (valid payload per schema, missing-field, unknown-field, 10 unknown-enum cases, 5 placeholder-ID cases, semantic constraints, DSL rejects `eval`-style nodes). Every CLI JSON report is self-validated against `report.schema.json` before printing. |
| T02 | Unicode-column and exclusive-end cases | `tests/unit/test_spans.py`: `größe / b` after `"Δ≠0"` gets byte columns that differ from character columns; exclusive end; CRLF, lone CR, BOM; multi-line spans; empty span; sha256 digests. `examples/python_demo/adversarial/unicode_names.py` gives a validated witness `{'größe': 0, 'b': 0}`. |
| T03 | each fixture identifies domain, expected evidence and ground truth | `tests/semantic_fixtures/manifest.json`: 24 cases + 1 skip case + 4 deferred cases. Each has case ID, language/version, paths, property, domain, expected result with and without solver, scope, ground-truth method/detail, environment lock, seed and origin/license. Expected values were derived from Python semantics, and `tests/semantic_fixtures/test_manifest.py` checks them. |
| T04 | tested host version and selected dependencies recorded | this file (versions table), `pyproject.toml` pins, `requirements.lock`, `docs/DECISIONS.md` |
| T10 | top-level side-effect sentinel remains untouched | `tests/integration/test_static_boundary.py::test_side_effect_sentinel_untouched_subprocess` and `_in_process`: a module that writes `SENTINEL_TOUCHED`, imports a nonexistent module and calls `sys.exit` is analyzed (conditional witness found). The marker file does not exist afterwards and no sentinel module enters `sys.modules`. |
| T11 | branch/source mappings validated | `tests/unit/test_lowering.py`: docs/04 ratio CFG shape and flavors, PHI at joins with predecessor-matched incoming, maybe-unbound PHI, short-circuit/chained branches, division span `(4, 15, 22)` = bytes `100 / d`, every syntactic division accounted for (9/9 in a mixed module), unsupported regions, IR JSON and version rejection |
| T12 | joins, bottom and safe/unsafe boundary tests | `tests/unit/test_intervals.py` (top/bottom/join/meet, infinite bounds, 0*inf, separate nonzero fact, nonzero endpoint shrinking, refinement boundaries incl. `x>5 AND x<3` = bottom, Python floor/mod by constants) |
| T13 | ratio safe/unsafe/annotation-only distinct | manifest cases `A-baseline-guarded` (PROVED_SAFE EXACT_MODEL), `A-candidate-unguarded` (PROVED_UNSAFE EXACT_MODEL, ERROR), `annotation-only` (PROVED_UNSAFE CONDITIONAL, INFERRED, WARNING); `test_annotation_only_never_becomes_unconditional` |
| T14 | SAT/UNSAT/UNKNOWN/unavailable accurate | `tests/unit/test_solver.py` uses real z3 5.1.0 for SAT, UNSAT, TIMEOUT (`reason_unknown == "timeout"`), RESOURCE_LIMIT (rlimit), the exact floor-division encoding (unique witness `a=-1`) and havoc making a query inexact. Unavailability is tested via `sys.modules["z3"]=None` and, in `test_solver_unavailable_is_distinct_from_disabled`, via an ImportError stub on `PYTHONPATH` in a subprocess. Witnesses are re-validated by the IR evaluator, and `test_witness_disagreement_is_an_engine_error` shows a disagreement becomes ERROR. |
| T15 | external-transform case is UNKNOWN and required incomplete | `test_fixture_c_unknown_call_is_incomplete`: UNKNOWN, no witness, listed in `coverage.unknown_obligations`, verdict INCOMPLETE, exit 2 |
| T16 | stdout schema valid, stderr logs separate | `test_demo_json_is_schema_valid_and_stdout_is_pure_json`, `test_human_format_on_stdout_and_logs_on_stderr`, exit-code tests for 0/1/2/3/4, determinism test (identical reports apart from run_id/timing) |
| T17 | real source -> real findings; no mocked solver or hard-coded outputs | Demo run below. `tests/differential/test_witness_replay.py` re-executes every PROVED_UNSAFE witness in an isolated `python -I` (or `-I -O`) subprocess and confirms ZeroDivisionError at the reported line. It also checks that PROVED_SAFE obligations never raise at their own line on a boundary grid, and that the IR evaluator agrees with CPython on 162 input pairs. Mocks are used only to inject engine faults. |

### M2

| Task | Acceptance check | Evidence |
| --- | --- | --- |
| T20 | staged/unstaged/untracked/deleted paths included | `test_verify.py::test_staged_unstaged_untracked_deleted_and_renamed_files_participate`: one throwaway repo with a staged deletion, a staged rename, a staged-then-modified file (the candidate uses the working-tree bytes), an unstaged modification, an untracked file and a git-ignored untracked file (excluded). Labels are checked (`untracked`, `deleted_staged`, `unstaged`, `staged`+`unstaged`, `staged_new`), and the resulting findings are INTRODUCED, PRE_EXISTING (renamed) and RESOLVED (deleted; span tied to the base snapshot). Non-Git: `test_baseline.py` (`baseline create` with and without `--retain-source`, `--rev`). Mid-run edits: `test_static_boundary.py` (restart once; file added mid-run detected) and `test_verify.py::test_verify_restarts_once_then_reports_incomplete`. |
| T21 | renames/deletions unresolved cases disclosed | `test_certain_and_uncertain_symbol_renames`: a certain rename (identical body) is matched, giving PRE_EXISTING; an uncertain rename (same params, changed body) is listed in `patch.symbols.uncertain_renames` with relation UNDETERMINED and run INCOMPLETE. Deleted files and symbols give RESOLVED; exact file renames are matched through `patch.files.renamed`; unresolved dynamic call edges are listed in `patch.graph.unresolved_dynamic_edges`. |
| T22 | changed-callee fixture catches unchanged caller | Same module: `test_fixture_d_changed_callee_invalidates_unchanged_caller`. Only `denominator` changes; `calc.py::scaled` is invalidated ("calls calc.py::denominator (changed)"); PROVED_UNSAFE EXACT_MODEL, witness `{'x': 0}`, INTRODUCED, ERROR, BLOCK, exit 1. The same witness is replayed against `git show BASE:calc.py` (returns 100.0) and the candidate file (ZeroDivisionError at the reported line). Cross-module: `..._cross_module_...`, where the importer is invalidated and the result is CONDITIONAL INTRODUCED WARN. Demo: `examples/patch_demo/run_demo.py`. |
| T23 | dependent summary invalidates | Global constant: `test_changed_global_limit_rechecks_dependent_index` (unchanged `pick` invalidated: "reads lim.py::LIMIT"). Type: `test_type_dependency_rebinding_int_invalidates_annotated_function` (`int = float` makes the annotation untrusted, so UNKNOWN). Config: `test_config_domain_change_invalidates_only_dependent_summary` (1 hit, 1 miss). Lock: `test_dependency_lock_change_invalidates_every_summary` (`global_change` mentions locks). Engine/solver/bounds: `test_cache.py::test_engine_or_solver_change_invalidates`. **Contract dependencies:** behavioral contracts do not exist before M3. The validity key reserves a fixed contract-set slot (`impact/keys.py::CONTRACT_SLOT`); invalidation by contract revision can only be tested in M3/T38. |
| T24 | stale or partial evidence never reused as pass | `tests/integration/test_cache.py`: warm == cold (identical results; reused evidence keeps its original run, snapshot and spans); KeyboardInterrupt mid-run leaves only `pending` entries and a `running` run, so the next run gets 0 hits; an inconsistent run publishes 0 entries (`failed`); a tampered artifact (PROVED_UNSAFE rewritten to PROVED_SAFE) fails its digest check and the symbol is recomputed (still PROVED_UNSAFE INTRODUCED); a doctored anchor is rejected; a comment-only edit reuses analysis with remapped current spans. |
| T25 | negative indexes, empty list and unknown aliases tested | `tests/integration/test_m2_semantics.py` (36 cases, every PROVED_UNSAFE replayed in CPython at the exact line): empty list, `xs[-1]` empty and guarded by `len` and by truthiness, `xs[-3]` with `len >= 2`, relational `0 <= i < len(xs)` and its off-by-one, literals, tuples across unknown calls, `len` taken before a mutation, alias mutation (UNKNOWN), Optional/union annotations, narrowing, None via a callee, and a regression for the I-051 false proof. Manifest cases `empty-list`, `negative-index-*`, `alias-mutation`, `none-flow-*`. Random differential: `tests/differential/test_m2_differential.py`. |
| T26 | same witness checked against both models | `impact/attribution.py` evaluates the candidate witness in the base IR at the anchor-matched site; every verify finding carries `comparison.witness_in_base`. INTRODUCED (Fixture D, base PASSED), PRE_EXISTING (`FAILED_SAME_SITE`), CHANGED (`test_pre_existing_changed_and_full_mode`: base fails at `a=1,b=2`, candidate witness `a=2,b=2` passes in the base, ERROR), RESOLVED (`test_resolved_when_candidate_proves_safe`, deleted symbols, None-flow repair via the evaluator's attribute anchor), UNDETERMINED (uncertain rename, base without source). |
| T27 | no Git checkout or silent baseline update | `test_verify_never_mutates_the_working_tree_or_git_state`: bytes, modes and mtimes of every file outside `.guard` state, including all of `.git`, are identical after quick and full runs; HEAD and `git status` are unchanged; new files appear only under `.guard/{state.sqlite*,artifacts,snapshots}`. `test_external_state_dir_leaves_repository_byte_identical` checks the same with `--state-dir`. Baselines: `test_baselines_are_immutable_and_never_silently_updated` (re-create refused, exit 4; a PASS verify leaves the table unchanged; SQL UPDATE/DELETE abort; `--supersedes` creates a new record). Base never inferred: `test_base_is_never_inferred_and_errors_are_usage_errors`. Quick/full: `test_pre_existing_changed_and_full_mode`; the quick budget overrun gives INCOMPLETE with a "--mode full" offer. |
| T28 | no required-obligation mismatch; actual timing recorded | `tests/integration/test_incremental.py`: 15 docs/07 change cases (function body, callee body, signature, shared constant, lock file, module initializer, new/removed file, removed callee, renamed file, renamed callee module, recursion, unresolved callback, comment-only, rebinding `len`) plus 6 seeded random 4-module projects with random mutations. For each: quick == cold full on every quick obligation (result, scope, witness, relation); every obligation outside the slice equals an independent `guard check` of the base tree; warm == cold; `widened_by_key_check` empty. Extra sweeps: 100 more random seeds (40 + 60), all consistent. Timing: see "M2 measurements". |

### M3

All dynamic results below come from real executions: Docker containers (`tests/integration/test_docker_isolation.py`, `examples/contract_demo/run_demo.py`) or the explicitly unisolated host backend (`test_contracts_dynamic.py`, faster, labelled UNISOLATED). Nothing is mocked except where a test injects a fault (a fake `docker` CLI, an interrupt, an edit).

| Task | Acceptance check | Evidence |
| --- | --- | --- |
| T30 | invalid, vacuous and malicious expressions rejected | `tests/unit/test_dsl.py`: the docs/06 example validates; `gt` canonicalizes to `lt` and both spellings have the same semantic digest; 42 parametrized rejections with their codes (unknown nodes `call`/`eval`/`ge`, smuggled fields, string/huge/NaN literals, bool as number, unknown or injected names, non-object nodes, depth 300 and 5,000, >64 nodes, >32 operands, missing NaN policy, negative tolerance, contradictory bounds, repeat budget, approximate repeatability, two repeat nodes, behavior nodes in a precondition, non-boolean and vacuous predicates, unsatisfiable precondition = INVALID_CONTRACT, unknown or missing profile, missing or unknown provenance, unknown symbol/file, method/decorated/`*args`/async targets, unknown top-level field, schema version, examples outside the profile); oversized and non-finite files; evaluator semantics (approx_eq NaN/infinity/require_finite, exact categories, Kleene logic, budget, dyn mismatch FALSE vs opaque UNKNOWN, no user `__eq__`, exact encodings for `unchanged_argument`/`repeatability`, CPython `sum`). `tests/unit/test_values.py` (exact round trips incl. -0.0/NaN/1 vs 1.0/tuples, harness and host encoders agree, opaque for custom objects/IntEnum/list subclasses/huge ints/cycles without calling user code, malformed nodes rejected). `tests/unit/test_profiles.py` (membership, deterministic admission, all-zero example excluded, tiny domains terminate, hints). |
| T31 | approval separate from tested/proved result | `tests/integration/test_contracts_lifecycle.py`: explicit audited transitions with reasons and invalid ones refused (exit 4); reject keeps history and removes policy effect; **approval creates no evidence and evidence never changes authority** (an INFERRED regression stays INFERRED and WARNs; after `approve` the evidence count is unchanged and the same revision then BLOCKs); revisions/events/evidence are append-only (UPDATE/DELETE abort); a hand-edited revision file is an integrity error (contract unusable, required check INCOMPLETE, approve refused); unindexed files are ignored until `import`; invalid declarations write nothing; state schema v1 migrates additively to v2 and a newer version is refused. |
| T32 | sources and assumptions retained | `tests/integration/test_templates.py`: purity from a doctest-typed function (provenance template + docstring, assumptions, source span), none for an in-place sort (reason reported), length and assertion templates, no repeatability for `random` users, unknown domains skipped not guessed, a top-level-write sentinel module never executed, schema-valid CandidateBatch, everything INFERRED with a `propose` event; dry runs deterministic and write nothing; a second run reports `exists`; `--model local` exited 4 in M3 (superseded in M4: it now requires `[semantic]` configuration, I-088). |
| T33 | disabled/static default and fail-closed backend behavior | `tests/integration/test_execution_failclosed.py`: `--execute` needs an enabled profile, `--run-profile` needs `--execute`, unknown profiles and unisolated profiles without `allow_unisolated` are usage errors (exit 4); a missing docker CLI gives RUNNER_UNAVAILABLE, required contract UNKNOWN, INCOMPLETE (exit 2), and the unisolated backend is provably not called; a fake docker whose capability probe reports `deny_network` unverified gives RUNNER_UNAVAILABLE after exactly one `run` (the probe; flags `--network none --read-only --pull never --cap-drop ALL` checked) and no target probe; a missing image says `guard runner setup` and nothing is pulled; `guard runner probe` exits 2/0; inferred-only contracts make an unavailable runner a WARN; the docker CLI and container environments contain no caller secrets. `test_contracts_dynamic.py::test_static_default_never_starts_a_runner` (runner and process layer monkeypatched to fail; contracts NOT_RUN/UNVERIFIED; `capabilities.execution = disabled`). |
| T34 | resource/network/host-write tests match claimed capabilities | Real containers, `tests/integration/test_docker_isolation.py`: all six capabilities VERIFIED from inside (no interface up, connect ENETUNREACH, read-only source, EROFS outside `/tmp`, uid 65534 with CapEff 0, cgroup memory/pids/cpu and RLIMIT_CPU as configured, interpreter 3.13); a probe's TCP connect gets ENETUNREACH and DNS fails; writes to the source mount, harness, `/etc`, `/` and two host paths fail and nothing appears on the host, while `/tmp` is writable; `IG_TEST_SECRET`/`AWS_SECRET_ACCESS_KEY` set in the caller are absent and `HOME=/tmp`; a 512 MiB allocation under a 128 MiB limit crashes or raises, a fork loop stops below the PID limit, an endless loop is killed as `timeout`; no `ig-probe-*` container is left. `tests/integration/test_runner_boundaries.py`: output limit, cyclic results UNKNOWN, unallowlisted target NOT_ALLOWED (INCOMPLETE), workspace path traversal refused, process-group kill on timeout, cancellation (KeyboardInterrupt) removes workspaces and publishes no evidence or cache entries. |
| T35 | positive-sum normalize witness passes base and fails patch | **Through containers** (`test_docker_isolation.py::test_fixture_b_through_containers` and `examples/contract_demo/run_demo.py`): approved `normalize.sum.v1`: OBSERVED_REGRESSION EXECUTED INTRODUCED, ERROR, BLOCK (exit 1); the example `xs=[1.0, 1.0]` returned `[0.5, 0.5]` in the base container and `[1/3, 1/3]` in the candidate container; minimized to `{'xs': [1]}` inside the domain; confirmations base PASS, candidate FAIL twice; `xs=[0.0, 0.0]` excluded by the precondition; a zero-only contract is OBSERVED_FAILURE PRE_EXISTING (WARNING); a length contract is TESTED. Unisolated equivalents and determinism (identical results and evidence IDs across runs) in `test_contracts_dynamic.py`. |
| T36 | observations remain finite; domain-preserving shrink | Fixture E (`sorted_copy`, `unchanged_argument`): OBSERVED_REGRESSION from `[2, 1]`, minimized to an unsorted list whose post-call snapshot is sorted (containers and unisolated). Repeatability (3 repeats): base 1 distinct result, candidate 3 (module-level counter): OBSERVED_REGRESSION. Repeats are bounded (2..50), probes by `max_probes`, shrinking by 12x24. Shrinking keeps `len >= 3` and elements in [2, 60] end to end (`test_shrinking_preserves_the_contract_domain`), and a 20-seed property test of the shrink loop never leaves the precondition (`test_profiles.py`). |
| T37 | inferred change warns; approved replayed regression blocks | `test_fixture_b_positive_sum_regression_blocks` (DECLARED: BLOCK, "blocking contract regression"), `test_inferred_contract_change_only_warns` (INFERRED: WARNING, verdict WARN, exit 0), `test_approval_is_separate_from_evidence` (same revision WARN then BLOCK after approval), PRE_EXISTING WARNING, RESOLVED INFO (`test_base_only_violation_is_resolved_info`), missing baseline target UNDETERMINED + INCOMPLETE (never BLOCK), changed lock files UNDETERMINED + INCOMPLETE, timeouts and import errors UNKNOWN + INCOMPLETE ("not a program defect"), `require_coverage = false` makes undecided contracts WARN. Evidence is written to the append-only store only after a consistent snapshot (`test_evidence_is_recorded_only_for_consistent_runs`). |
| T38 | new predicate/domain cannot inherit old passing evidence | `test_revision_invalidates_old_evidence_and_is_audited`: TESTED evidence for r1; revising with identical semantics is refused; a looser tolerance is r2 (DECLARED kept, 2 stale evidence rows reported) with no current evidence and `latest_evidence = null`; the next run records r2 evidence while r1 evidence remains; a domain change is r3, again with no evidence. T23 contract dependency: `test_contract_revision_invalidates_only_the_target_summary` (declaring and revising re-key only `normalize`'s summary: 1 miss, 1 hit; retiring returns to the contract-free key, which is validly reused). |

### M4

The fake loopback server in `tests/model_helpers.py` drives the real adapter, transport, parser and pipeline in tests. The gate evidence for real inference is the qwen2.5-coder:7b run ("M4 measurements", `benchmarks/results/m4_model_eval.json`) plus the CLI demonstration below. No test or measurement uses a mocked model as evidence of model quality.

| Task | Acceptance check | Evidence |
| --- | --- | --- |
| T40 | bounded source selection and candidate schema | `tests/unit/test_context_packet.py` (7):<br>- the packet is schema-valid (`context_packet.schema.json`) and contains exactly the target, its callee, two callers (same module and `from`-import) and the one test that mentions it;<br>- an unrelated function's marker, an unrelated test and a side-effect module are absent, and the sentinel file is never written (AST only);<br>- deterministic digest;<br>- a tight byte budget omits lower-priority items and states it; a target over budget (bytes or tokens) is `CONTEXT_TOO_LARGE`, never truncated;<br>- unsupported constructs are listed (`np.mean`, try/except);<br>- untrusted framing: the injected docstring sits inside its nonce-delimited block, the nonce is absent from the source and is re-derived when planted;<br>- the response schema has a const profile, no `authority` and `additionalProperties: false`.<br>Model batches validate against `candidate_batch.schema.json` (source `local_model`, model identity, packet digest): `tests/integration/test_model_discovery.py`. |
| T41 | redirect/non-loopback destinations rejected; no cloud fallback | `tests/unit/test_semantic_endpoint.py` (37):<br>- 6 loopback forms are accepted;<br>- 24 forms are rejected with codes: private, public and unspecified IPs, IPv4-mapped public, zone IDs, hostnames including `api.openai.com`, numeric shorthands, https/ftp, user info, path/query/fragment, missing or out-of-range port, whitespace, None;<br>- the config refuses remote endpoints even while disabled, as well as `download_during_scan`, other providers and unknown keys like `fallback`;<br>- a 307 to another loopback server is `REDIRECT_REJECTED` and the target is never hit;<br>- `HTTP_PROXY`/`ALL_PROXY` pointing at a recording proxy are ignored;<br>- `/api/pull`, `/api/create`, `/api/delete`, `/api/push`, `/api/generate` and `/v1/chat/completions` are refused before any connection;<br>- unreachable gives `UNREACHABLE`, more than 1 MiB gives `RESPONSE_TOO_LARGE`, an absent model gives `MODEL_ABSENT` (no pull);<br>- the digest pin is enforced;<br>- the chat request uses a JSON-schema `format`, `stream: false`, no tools.<br>A socket audit fixture (`loopback_only`) records every destination in the M4 tests: all are 127.0.0.1, and a non-loopback connect fails the test. `test_audit_regressions.py::test_model_transport_is_loopback_http_client_only` pins `semantic/endpoint.py` as the only network module (`http.client`, `socket`, `urllib.parse`; no `urlopen`/https/name resolution). Fault injection: disabling the loopback check fails 6 tests; disabling the 3xx check fails 1. |
| T42 | revision/license/runtime/hardware recorded; actual inference demonstrated | Host table above (digest, Apache-2.0 license text sha256, Ollama 0.35.1, GPU placement, hardware). `guard model status` and the discover output report the identity read from the runtime. Real inference: the evaluation ran 3 x 13 real `/api/chat` calls ("M4 measurements"), plus the CLI demonstration in "M4 measurements". |
| T43 | model output cannot produce proof/approval on its own | `tests/unit/test_model_candidates.py` (42): 8 malformed whole answers (prose, truncated, NaN, 100k nesting, wrong shape, extra top-level `authority`, fenced JSON) and 30 hostile candidates, each rejected with its code:<br>- prompt injection obeyed (`authority`, `proof`, `lifecycle_state`, `evidence_ids`): FORBIDDEN_FIELD;<br>- Python, SMT, code in literals, a `python` node: UNTYPED_TEXT;<br>- lambda as a kind: UNKNOWN_NODE;<br>- nonexistent parameters: UNKNOWN_SYMBOL;<br>- references to unsent snippets or lines: INVALID_SOURCE_REF;<br>- wrong profile: PROFILE_MISMATCH;<br>- false/contradictory preconditions: INVALID_CONTRACT;<br>- input-only predicate: VACUOUS;<br>- tautologies (`0 <= len(result)`, `x == None or not x == None`, `len(result) == len(result)`): VACUOUS_PREDICATE;<br>- behavior in a precondition: TYPE_ERROR; depth 17 and 10,000 repeats: BUDGET_EXCEEDED; malformed fields: MALFORMED_CANDIDATE;<br>- duplicates and more than the requested number of candidates.<br>Accepted candidates are INFERRED/VALIDATED with `local_model` provenance; a rationale claiming "PROVED ... APPROVED" changes nothing; the same non-vacuity filter accepts every template candidate and rejects a template-shaped tautology.<br>End to end (`test_model_discovery.py`, 8): a fake model obeying the docstring injection: the injected candidate is rejected, a correct one is TESTED and recorded INFERRED with no evidence, a wrong one is REFUTED with a minimized counterexample confirmed in a fresh run and not recorded, template candidates got identical treatment. A recorded model candidate broken by a patch is OBSERVED_REGRESSION with severity WARNING (never BLOCK), and `verify` made no request to the model. Fault injection (each reverted alone): removing FORBIDDEN_FIELD fails 5 tests, UNTYPED_TEXT 5, non-vacuity 4, the fresh-run confirmation 1. |
| T44 | checkability/usefulness/time measured on held-out examples | `benchmarks/model_eval.py` on `benchmarks/fixtures/m4_heldout` (13 functions written for this evaluation, 5 seeded docstring-contradicting bugs, references outside the scanned tree, no overlap with the prompt example). Real model, real Docker falsification, 3 model repeats and 3 template repeats: see "M4 measurements" and `benchmarks/results/m4_model_eval.json`. |
| T45 | deterministic path survives model absence | `test_model_discovery.py::test_model_off_paths_keep_deterministic_discovery`:<br>- endpoint down: exit 2, `model.status` UNAVAILABLE (UNREACHABLE), INCOMPLETE with a stderr reason, template candidates still recorded;<br>- the same config without `--model`: exit 0, model DISABLED;<br>- `guard check` works;<br>- absent model: MODEL_ABSENT, only `/api/version` and `/api/tags` requested;<br>- digest mismatch: no chat request.<br>`test_malformed_model_output_is_counted_not_trusted` (an answered but malformed response is counted, not an outage). `test_raw_context_retention_is_off_by_default` (no `model_context` artifact by default; with `store_raw_model_context = true` one per function, containing packet and prompt). `test_recorded_model_candidate_...` (verify never contacts the model). `test_templates.py::test_model_discovery_requires_explicit_semantic_config` (exit 4 without `[semantic]`, nothing contacted). |

### M5

| Task | Acceptance check | Evidence |
| --- | --- | --- |
| T50 | protocol output/log separation and schema compatibility | `tests/integration/test_mcp_server.py`. The server runs as a real subprocess in every test. Test functions:<br>- `test_stdout_carries_only_protocol_frames`: the wrapper injects `print()`, `sys.__stdout__` writes and a child process writing to the inherited fd 1 on every analyzed function. Every stdout line is a JSON-RPC 2.0 frame, the three noise strings appear only on stderr, and verify (BLOCK) and check (OK) responses arrive.<br>- `test_modern_protocol_and_every_tool`: all six tools over the 2026-07-28 protocol. The full report from `detail="full"` validates against `report.schema.json`.<br>- `test_findings_are_fetchable_and_marked_stale_after_the_repair` (`test_agent_service.py`): `get_finding`'s finding and evidence validate against `finding.schema.json`/`evidence.schema.json`.<br>- `test_core_does_not_import_mcp_at_startup`: `guard capabilities` plus imports of the service and verify modules load no `mcp`, `mcp_types`, `starlette`, `httpx`, `uvicorn` or `anyio` module.<br>- `test_server_process_opens_no_network_connection`: socket connect, bind and listen are booby-trapped in the server process; verify succeeds and nothing was attempted. |
| T51 | default tools cannot approve contracts/baselines | `tests/integration/test_agent_service.py`:<br>- `test_tool_set_has_no_approval_baseline_or_execution_tools`: exactly six tools, closed schemas, and the union of all arguments is the ten listed names; unknown tools are INVALID_REQUEST.<br>- `test_contract_authority_is_unchanged_by_every_tool`: after verify (quick/full), check and capabilities, contract IDs, authority, lifecycle and revision are identical, there is 1 contract event (the maintainer's declare) and 0 baselines.<br>- `test_only_configured_roots_and_inside_paths_are_served`: other roots, subdirectories as repository, `../other`, symlink escapes, absolute paths and an option-like base are refused, and `git status` shows nothing new except the test's own symlink.<br>- `test_run_profiles_must_be_preauthorized_isolated_and_unchanged`: profiles that were not allowed, profiles edited after startup and unisolated profiles are NOT_AUTHORIZED.<br>- `test_config_is_read_only_from_the_root_never_a_parent`: the CLI picks up a parent's config (EXACT_MODEL), while the service ignores it (CONDITIONAL).<br>- `test_root_specs_are_validated`.<br>Through MCP, `test_modern_protocol_and_every_tool` covers NOT_AUTHORIZED for a foreign root and INVALID_REQUEST for `approve_contract`, and the CLI confirms the authority is still DECLARED. |
| T52 | cancelled/stale runs cannot become passing cache entries | Service-level tests in `test_agent_service.py`:<br>- `test_cancelled_verify_publishes_no_cache_entries_evidence_or_report`: a token cancelled during the 4th analyzed function stops the run at the next per-symbol safe point. The run row is `failed` with note `cancelled: test cancellation`, there are 0 complete cache entries and 0 indexed reports, and the next verify has 0 cache hits and BLOCK.<br>- `test_cancellation_kills_a_running_process`: `sleep 30` is killed within 5 s, and no process starts once cancelled.<br>- `test_stale_snapshot_during_a_run_is_an_explicit_status`: edits during both attempts give STALE_SNAPSHOT, run_outcome INCOMPLETE, a message to re-run, and 0 complete cache entries.<br>MCP tests in `test_mcp_server.py`:<br>- `test_client_cancellation_finalizes_the_run_as_failed`: the client abandons after 1.5 s, and the run is `failed`/`cancelled: client cancelled the request` with nothing published; the server keeps serving.<br>- `test_server_timeout_is_an_explicit_response_and_publishes_nothing`: `timeout_s=1` gives TIMEOUT (`isError`), the run is `failed`/`cancelled: timeout after 1 s`, and the next full run has 0 cache hits.<br>Evidence fetching: `test_findings_are_fetchable_and_marked_stale_after_the_repair` shows CURRENT, then STALE_SNAPSHOT after the repair, then `superseded_by_run` after re-verify, plus NOT_FOUND and INVALID_REQUEST for a path-like ID. `test_check_scope_findings_live_in_the_session` and `test_guard_explain_reads_recorded_cli_reports` (also: DELETE on `run_reports` aborts as append-only) complete the coverage. |
| T53 | exact tested integration instructions | `docs/MCP_INTEGRATION.md` (Claude Code project `.mcp.json`). `test_documented_claude_code_config_launches_and_verifies` extracts that JSON block, launches its exact command and args with the SDK stdio client (initialize handshake, protocol 2025-11-25), and checks:<br>- the server info and instructions;<br>- `tools/list` (six tools, annotations, required inputs);<br>- `verify_patch`: BLOCK, exit 1, INTRODUCED PROVED_UNSAFE EXACT_MODEL at `demo/math_ops.py:3:12` with a validated witness where a == b;<br>- `get_finding` (schema-valid).<br>Not tested: a live Claude Code session. |
| T54 | actual unsafe patch, generated evidence, real repair and final snapshot report | `examples/demo_repo/recorded_run/` (36 files, write-once) from `run_demo.py --out examples/demo_repo/recorded_run`; see "M5 repair loop". Agent client: the local-model harness over MCP, not Claude Code. |
| T55 | M0-M5 gates, accurate limits, offline and model/no-model paths | `docs/ALPHA_CHECKLIST.md`. |

### M7 (partial)

| Task | Acceptance check | Evidence |
| --- | --- | --- |
| T73 | actual precision/recall/abstention/latency with denominators | `benchmarks/run_all.py` (all parts exit 0; `benchmarks/results/paper_manifest.json`). TP/FP/FN/TN and abstentions are reported with denominators: per case and per class for 35 seeded pairs, and over 28 labeled semantic fixtures. Ablations: solver on/off, quick vs full (mismatch count and time), warm vs cold, static-only vs dynamic-only vs combined, templates vs local model vs agent proposals. Latency: p50/p95 and peak RSS at about 100, 1,000 and 10,000 functions, with n = 30 (5 at 10,000). Published as `paper/invariantguard.tex`; numbers are in "Paper benchmarks and report". T72 (held-out real-agent patch benchmark) remains open. |

## Milestone gates (docs/11)

**M0 gate.**
- Package installs in a clean local environment: yes (T00 evidence).
- Schema invalid/unknown cases fail validation: yes (T01).
- Tests run without model or network: yes. No model code exists, and the autouse fixture in `tests/conftest.py` makes any in-process socket connect fail the test.
- Source maps and snapshot identity are represented: yes (`SourceSpan`, `snapshot.schema.json`, content-addressed `snapshot_id`).

**M1 gate.**
- Safe/unsafe/unknown fixtures produce actual semantic outcomes: yes.
- No repository import occurs: yes (sentinel).
- Solver unknown and engine error are distinct: yes. TIMEOUT and RESOURCE_LIMIT give UNKNOWN and exit 2, while an injected analyzer, lowering or witness fault gives ERROR and exit 3.
- A conditional domain never becomes unconditional through annotations: yes.
- The concrete witness for the unsafe ratio and the guarded safe obligation are demonstrated below.
- No patch attribution is claimed: `patch_relation` is always UNDETERMINED and `base_snapshot` is null.

**M2 gate.**
- Unchanged callers are rechecked when a callee changes: yes (T22, same and cross module; the graph reason is recorded per symbol).
- New/untracked/deleted files participate: yes (T20).
- Incremental and full required obligations agree on fixtures: yes (T28; 0 mismatches in 21 suite scenarios and 100 extra random seeds).
- Stale caches fail closed: yes (T24).
- No checkout or working-tree mutation occurs: yes (T27; byte-, mode- and mtime-identical, including `.git`).
- Static timing and memory are measured: yes (below; real runs on this host, cold and warm separately).
- Unsupported dynamic edges are captured: yes (`patch.graph.unresolved_dynamic_edges`, with the stated havoc policy, I-042).

**M3 gate.**
- Normalize regression shows base pass and candidate fail on the same positive-sum input: yes, through real containers (T35). `xs=[1.0, 1.0]`: base `[0.5, 0.5]`, candidate `[1/3, 1/3]`; minimized `[1]` was confirmed in fresh containers.
- Invalid-domain and both-fail cases are not regressions: yes. The all-zero input is excluded and never probed; both-fail is PRE_EXISTING (WARNING).
- Passing probes say TESTED: yes, with scope EXECUTED and a finite quantifier, and no finding.
- Runner unavailable means INCOMPLETE when dynamic checks are required: yes, with no unisolated fallback (T33).
- The dual-engine architecture exists: static obligations and dynamic contracts are combined in one report and one policy. Contracts are declared or template-mined; model inference is M4.

**M4 gate.**
- Real model-generated candidates enter the same mechanical pipeline as deterministic candidates: yes. The same `validate_proposals` (revision validation + non-vacuity), de-duplication, falsification and recording are used for both sources. The qwen2.5-coder:7b answers on the held-out set were classified by that pipeline ("M4 measurements").
- Invalid, vacuous and adversarial suggestions are rejected: yes (T43). Real model output produced real rejections too (TYPE_ERROR, VACUOUS_PREDICATE, DUPLICATE; see measurements).
- No cloud or network fallback occurs: yes (T41). The endpoint is loopback-only and validated, redirects and proxies are refused, there is no other code path, and the socket audit saw 127.0.0.1 only.
- Model-off remains functional: yes (T45).
- One actual open-weight artifact is configured with its tested license and runtime identity recorded: yes (host table, T42).
- Not met and disclosed: docs/08 asks to evaluate at least two open-weight options. Only one was evaluated, because a second would need a download, which was not authorized (I-089). No model is pinned as a general "recommendation".

**M5 gate.**
- Stdio MCP, shared APIs, cancellation and compact evidence retrieval: yes (T50-T52). The six tools call the CLI orchestration, and compact results (about 6 KB in the recorded run, versus a 63 KB full report) carry IDs for `get_finding`.
- One tested client integration is documented: yes, the Claude Code project `.mcp.json`. It was tested by launching the documented command with the SDK client: handshake, `tools/list`, `verify_patch`. A live Claude Code session was not tested; the recorded loop's agent is the local-model harness.
- patch -> verify -> failure evidence -> repair -> verify is demonstrated: yes (recorded run).
- The report tracks the final snapshot: yes. The final MCP report and the final CLI report have the same candidate snapshot `sha256:66d3969c…`, which differs from the candidate's `sha256:05008e6f…`.
- No stale evidence is attributed to the repaired code: yes. After the repair, both pre-repair findings return STALE_SNAPSHOT with `superseded_by_run` set to the PASS run (recorded files 25-26), and tests cover this.
- Unknown required work prevents PASS: yes.
  - In the recorded run, the whole-snapshot no-model `guard check` is INCOMPLETE (exit 2) because `normalize`'s list-comprehension division is UNKNOWN.
  - The final quick verify is PASS because the restored `normalize` equals the base and lies outside the patch slice; that is disclosed in its verdict reasons.
  - Tests: `test_unknown_required_work_is_incomplete_not_pass_through_the_service`; RUNNER_UNAVAILABLE gave INCOMPLETE in a trial run (see measurements).
- The agent never auto-approves a baseline or contract change: yes. No such tool exists (T51), and the recorded run's contract stays DECLARED r1 with one event.
- The complete loop is measured separately from verifier latency: yes ("M5 repair loop").
- Hook/CI enforcement: not implemented. MCP alone is not enforcement (`docs/MCP_INTEGRATION.md`).

**M6 gate.** Not started: DEFERRED. There is no JDK or Kotlin toolchain on the host, and a download is not authorized. The written feasibility decision is I-101.

## Validation commands and actual outputs

```text
# Remote and agent proposers (8 October 2026, engine 0.5.0)
$ .venv/bin/python -m pytest -q                                # Docker daemon NOT running in this session (Docker Desktop not started):
779 passed, 11 skipped, 1 xfailed in 172.34s                   #   9 Docker tests skipped honestly + 2 opt-in real-model tests; the xfail is the known U02 case
  baseline before this change, same host state: 718 passed, 10 skipped, 1 xfailed in 133.48s
$ .venv/bin/python -m pytest -q tests/unit/test_remote_adapters.py tests/integration/test_remote_discovery.py \
      tests/integration/test_agent_proposals.py tests/integration/test_agent_eval_tools.py
  -> 59 passed (44 + 8 + 5 + 2); test_mcp_server.py::test_agent_proposals_over_mcp passes over stdio in both the legacy
     (initialize) and the 2026-07-28 protocol, with the client's clientInfo recorded as provenance
$ IG_REAL_MODEL=1 .venv/bin/python -m pytest -q tests/integration/test_real_model.py -k openai_compatible
1 passed in 67.91s   (REAL run: OpenAI-compatible adapter -> Ollama 0.35.1 http://127.0.0.1:11434/v1, qwen2.5-coder:7b,
                      structured output json_schema accepted, served_model qwen2.5-coder:7b, --execute with the unisolated profile)
$ guard contracts discover --model local --dry-run   (manual smoke, provider = "openai_compatible", base_url = "http://127.0.0.1:11434/v1")
  -> exit 0; 1 function ANSWERED in 98.3 s (cold model load); 1387 prompt / 370 output tokens; 2 model candidates
     (1 duplicate of a template claim, 1 would_propose); egress: context_left_machine false, packet 1490 bytes, request 10797 bytes
$ guard capabilities --path <repo with provider = "anthropic", remote_allowed = true>
  -> configured proposer: anthropic claude-sonnet-5-5 at https://api.anthropic.com; REMOTE ... + WARNING (retain or train on submitted data)
$ guard contracts discover --model local --path <same repo>   -> exit 4 "--model local refuses the configured anthropic provider at api.anthropic.com ..."
$ guard check <repo with provider = "anthropic" and no remote_allowed> -> exit 4 "semantic.base_url: REMOTE_NOT_ALLOWED ..."
$ .venv/bin/python benchmarks/agent_eval/make_packets.py      -> 13 packets; regenerated in a second temp workspace: index.json identical
$ .venv/bin/pip freeze --exclude-editable | diff - <(grep -v '^#' requirements.lock)   -> no difference (no new dependency)
Hosted providers (OpenAI, Anthropic, ...): NOT exercised (no API key on this host).

# M5 (8 October 2026, engine 0.4.0)
$ .venv/bin/pip install "mcp==2.3.0"                          -> installed mcp 2.3.0 + 24 transitive packages; requirements.lock regenerated (pip freeze equals the lock)
$ .venv/bin/python -m pytest -q -rs                            # Docker daemon reachable (docker info 5.1 s), image present
699 passed, 1 skipped in 175.66s   (skip: the opt-in real-model test; no ig-* containers left afterwards)
  after the last code change (mcp_server.py: client cancellation and unexpected handler failures are logged separately;
  log-only, made after the recorded run, whose normal path is unchanged): 699 passed, 1 skipped in 177.51s
$ .venv/bin/python -m pytest -q tests/integration/test_mcp_server.py tests/integration/test_agent_service.py
  -> all 21 pass (8 + 13), including Docker replay through a preauthorized profile, client cancellation and server timeout
  (an earlier full run during host load got "docker daemon not available: timed out" and skipped the 8 Docker isolation tests honestly;
   re-run with the daemon responsive: test_docker_isolation + test_mcp_server + test_contracts_dynamic = 32 passed in 85.72s)
$ .venv/bin/guard mcp serve --stdio < /dev/null                -> exit 4 "at least one repository root is required (--root [ALIAS=]PATH)"
$ .venv/bin/guard capabilities                                 -> mcp: available (explicit) - guard mcp serve --stdio ... (mcp 2.3.0)
$ .venv/bin/python examples/demo_repo/run_demo.py --out examples/demo_repo/recorded_run
  -> exit 0, 173.1 s; candidate BLOCK (exit 1) -> 1 model repair attempt -> MCP verify PASS (exit 0) -> pre-repair findings STALE_SNAPSHOT
     -> final CLI verify PASS (exit 0, same snapshot sha256:66d3969c…) -> no-model check INCOMPLETE (exit 2) -> discovery exit 0
     (details: "M5 repair loop (recorded run)")

# Earlier milestones (unchanged below)
$ .venv/bin/python -m pytest -q          # includes the real-container tests (Docker daemon + prepared image present)
536 passed in 95.55s
  (no ig-probe-* containers and no ig-* temporary workspaces left afterwards)

$ .venv/bin/guard runner setup --run-profile isolated-python    -> exit 0 (image python:3.13-slim, sha256:3dd7cc10…, CPython 3.13.16 in the image)
$ .venv/bin/guard runner probe --run-profile isolated-python    -> exit 0, AVAILABLE; deny_network, readonly_source, no_host_credentials,
                                                                  scratch_only_writes, resource_limits, interpreter_minor_matches_host: VERIFIED
$ .venv/bin/python examples/contract_demo/run_demo.py --setup   -> two guard verify --execute runs in a throwaway Git repo (Docker)
  candidate patch: verdict BLOCK (exit 1), run_outcome COMPLETE
    normalize.sum.v1 r1 [DECLARED] CHECKED OBSERVED_REGRESSION INTRODUCED severity=ERROR
      probes: admitted 48, excluded by domain 4, candidate pass/fail/unknown 0/48/0, runner invocations 9
      witness {'xs': [1]} (first observed {'xs': [1.0, 1.0]}); base: predicate holds [1.0]; candidate: predicate does not hold [0.5]
      excluded: {'xs': [0.0, 0.0]} (precondition is false)
    sorted_copy.pure.v1 r1 [DECLARED] CHECKED OBSERVED_REGRESSION INTRODUCED severity=ERROR
      witness {'xs': [1, 0]} (first observed {'xs': [2, 1]})
  after the repair: verdict PASS (exit 0), run_outcome COMPLETE; both contracts TESTED (48 admitted probes each)
$ .venv/bin/python examples/contract_demo/run_demo.py --backend unisolated -> same results, every capability NOT_ENFORCED (labelled unisolated)
$ .venv/bin/guard contracts discover --dry-run   (on tests/integration/test_templates.py's LIB module)
  8 candidates (purity, length, assertion, repeatability), all INFERRED; sort_in_place purity skipped ("calls xs.sort(...)"); opaque() skipped (no profile)

M0-M2 commands re-run on engine 0.3.0 with unchanged outcomes:

$ .venv/bin/guard check examples/python_demo/baseline --format json            -> exit 0 (PASS)
$ .venv/bin/guard check examples/python_demo/candidate --format json           -> exit 1 (BLOCK)
$ .venv/bin/guard check examples/python_demo/unknown/imported_ratio.py --format json -> exit 2 (INCOMPLETE)
$ .venv/bin/guard check examples/python_demo --format json                     -> exit 1 (BLOCK / INCOMPLETE)
  31 obligations over 28 files: 10 PROVED_UNSAFE, 9 PROVED_SAFE, 1 POSSIBLY_UNSAFE, 11 UNKNOWN
  (division 7/7/1/10, index.out_of_range 2/1/0/1, none.attribute_access 1/1/0/0)
$ .venv/bin/python examples/patch_demo/run_demo.py                             -> verify exit 1 (BLOCK)
  invalidated calc.py::denominator: definition changed
  invalidated calc.py::scaled: calls calc.py::denominator (changed)
  INTRODUCED ERROR PROVED_UNSAFE [EXACT_MODEL] calc.py::scaled::division.zero_denominator#0
  witness {'x': 0}; same witness in base: PASSED
```

Every PROVED_UNSAFE witness in the demo (including the new IndexError and AttributeError ones) is replayed by `tests/differential/test_witness_replay.py` in an isolated `python -I` process at the exact reported line.

The M1 excerpt below is kept for history. It was produced by engine 0.1.0; snapshot IDs and the obligation set changed with M2.

Excerpt of the real `guard check examples/python_demo --format json` stdout. Fields were selected by a script from the full report; values are unedited.

```json
{
  "schema_version": "1.0",
  "run_id": "run-11aa12ac9a334b8fb0225f608a213122",
  "base_snapshot": null,
  "candidate_snapshot": "sha256:a44e3fd05c170ab263db7de45090b96c5c3d31f31d33e013f88c9475bf0a0082",
  "run_outcome": "INCOMPLETE",
  "verdict": "BLOCK",
  "verdict_reasons": [
    "4 blocking finding(s): exact modeled failure in an admitted domain",
    "10 required obligation(s) UNKNOWN",
    "1 required check(s) skipped (e.g. unparsable file or changed source)",
    "3 advisory finding(s) (conditional or overapproximate)"
  ],
  "coverage": {"requested_symbols": 24, "analyzed_symbols": 21, "required_obligations": 24,
               "resolved_required_obligations": 14, "files_captured": 23, "files_analyzed": 22,
               "skipped_checks": [{"kind": "parse_failure", "path": "adversarial/syntax_error.py", "symbol_id": null,
                                   "reason": "syntax_error: SyntaxError: expected ':' (line 1)", "required": true}]},
  "timing_ms": {"capture": 1.804, "static": 145.814, "solver": 122.89, "dynamic": null, "model": null},
  "findings[candidate/ratio.py::ratio]": {
    "finding_id": "fnd-b5ae8de6730f249450e6acef7af0200c",
    "property_result": "PROVED_UNSAFE", "scope_validity": "EXACT_MODEL", "authority": "DECLARED",
    "patch_relation": "UNDETERMINED", "severity": "ERROR",
    "source_span": {"path": "candidate/ratio.py", "start_line": 3, "start_column": 11, "end_line": 3, "end_column": 18,
                    "start_byte": 52, "end_byte": 59,
                    "snapshot_id": "sha256:a44e3fd05c170ab263db7de45090b96c5c3d31f31d33e013f88c9475bf0a0082"},
    "quantifier": "exists admitted function input",
    "domain": "a, b: exact Python built-in integers",
    "assumptions": ["domain explicitly admitted in configuration: demo public API admits exact built-in integer inputs"],
    "path_constraints": ["d == a - b", "d == 0"],
    "witness": {"a": 0, "b": 0}, "witness_validated": true,
    "evidence_ids": ["ev-f881677f5e78e4dbc7d7d92cae04b57a"]
  },
  "obligation_results[baseline/ratio.py::ratio]": {
    "obligation_id": "baseline/ratio.py::ratio::division.zero_denominator#0",
    "property_result": "PROVED_SAFE", "scope_validity": "EXACT_MODEL", "method": "INTERVAL",
    "reasons": ["divisor d has interval/nonzero fact [-inf, +inf] ≠0, which excludes 0"]
  },
  "obligation_results[unknown/imported_ratio.py::imported_ratio]": {
    "obligation_id": "unknown/imported_ratio.py::imported_ratio::division.zero_denominator#0",
    "property_result": "UNKNOWN", "scope_validity": null, "method": "UNSUPPORTED",
    "reasons": ["divisor has type object: its value is not modeled; it comes from call to 'external_transform' (unresolved): result and effects unknown; call summaries are not implemented in M1"]
  },
  "evidence[candidate]": {"kind": "SOLVER", "witness_validation": "VALIDATED",
    "solver_runs": [{"solver": "z3", "solver_version": "5.1.0",
      "query_digest": "sha256:ed43f2e2a710f39642f0eb07754cae3fcad4b2d01a0b9dbc74e79c89794ff81e",
      "response": "SAT", "reason_unknown": null, "elapsed_ms": 0.969, "timeout_ms": 100, "rlimit": null,
      "exact_encoding": true, "assertions_constraint": "any"}]},
  "capabilities": {"solver": "configured", "solver_detail": "z3 5.1.0", "execution": "disabled", "model": "disabled",
                   "frontends": ["python (stdlib ast, grammar 3.13)"]}
}
```

Z3 chose the witness `a=0, b=0`. Any `a == b` is a witness, including `a=1, b=1` from docs/13. The differential test replays the reported witness in an isolated interpreter and observes ZeroDivisionError at `candidate/ratio.py` line 3.

Outcome of every demo fixture (with solver):
- PROVED_SAFE: baseline ratio, guarded_nonzero, unreachable_branch, two_variable_equality (SMT UNSAT), floor_semantics `a // 2`, both modulo_guard sites.
- PROVED_UNSAFE EXACT_MODEL: candidate ratio, floor_semantics `100 // (a + 1)` (unique witness `a=-1`, which depends on floor semantics), literal_zero (`{}`), unicode_names.
- PROVED_UNSAFE CONDITIONAL: annotation_only, assert_guard (only under `python -O`; the assertions-enabled query is UNSAT).
- POSSIBLY_UNSAFE OVERAPPROXIMATE: unknown_call_unrelated.
- UNKNOWN: imported_ratio, isinstance_guard, negative_index, local_helper_call, exception_handler, sum_of_cubes (solver TIMEOUT), float_nan, loop_total, decorated, method_division.
- Skipped (parse_failure): syntax_error.

Without a solver (`--no-solver`), two_variable_equality and unknown_call_unrelated become UNKNOWN. All other results are unchanged, and the witnesses come from the bounded search.

Robustness smoke run (not a benchmark, no ground truth): `guard check <miniconda python3.13 stdlib incl. site-packages> --root <same>` on 8,333 files. It finished with 0 engine errors, 0 parse failures and 154,678 requested symbols, of which 29,784 were analyzed. It found 26,102 division obligations: 152 PROVED_SAFE, 2 PROVED_UNSAFE, 2 POSSIBLY_UNSAFE and 25,946 UNKNOWN (mostly methods). Static analysis took 78 s. The two PROVED_UNSAFE results are genuine: boltons `excutils.whoops_math` (`1/0` on purpose, EXACT_MODEL literal-only) and jinja2 `tests.test_divisibleby` (`value % num` with `num=0`, CONDITIONAL on annotations).

## M2 measurements

Measured with `.venv/bin/python benchmarks/incremental_timing.py --reps 30 --reps-large 3 --scales 100,1000,10000` (raw per-run data: `benchmarks/results/m2_incremental_20261004_161451.json`). Host: Mac16,12 Apple M4, 16 GB RAM, 10 logical CPUs, macOS 15.6 (24G84), Python 3.13.9, z3 5.1.0, git version 2.52.0, invariantguard 0.2.0. Machine not otherwise loaded by this session during the run (other desktop processes were running).

The projects are synthetic and generated deterministically (seed 2026): modules of 25 functions importing a shared `pkg/core.py` (constants, a helper), with division, list-index, call-summary and arithmetic shapes, all with declared exact-int domains. Two candidate changes:
- `leaf`: one function body in one module changes (small slice);
- `hub`: `core.SCALE` changes (every function that reads it, directly or through a callee, is invalidated).

Definitions:
- *cold*: an empty state directory (no cache);
- *warm*: a state directory populated by one identical earlier run;
- *wall*: elapsed time of a fresh child process, including interpreter start and imports;
- *engine*: the sum of the report's own timing fields (capture, base capture, lowering + analysis, impact);
- *RSS*: the child's `ru_maxrss`;
- *cache hits*: the last run of the condition. In the 1,000-function hub warm condition, the first warm run analyzed the symbols the budget-limited populating run could not cache, and later runs hit all 570.

n = 30 per condition for about 100 and 1,000 functions; n = 3 for about 10,000 functions (docs/12: "at least 30 ... where feasible"; at that scale one full cold run takes minutes).

| functions | change | mode | cache | n | wall p50 / p95 (ms) | engine p50 / p95 (ms) | peak RSS p50 (MB) | affected symbols | verdict / run outcome | cache hits (base, candidate) |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 101 | leaf | quick | cold | 30 | 456 / 471 | 154 / 159 | 36 | 2 | PASS/COMPLETE | 0, 0 |
| 101 | leaf | quick | warm | 30 | 448 / 468 | 149 / 155 | 36 | 2 | PASS/COMPLETE | 2, 2 |
| 101 | leaf | full | cold | 30 | 1,374 / 1,438 | 910 / 972 | 77 | 2 | PASS/COMPLETE | 0, 0 |
| 101 | leaf | full | warm | 30 | 608 / 619 | 162 / 169 | 41 | 2 | PASS/COMPLETE | 101, 101 |
| 101 | hub | quick | cold | 30 | 798 / 858 | 424 / 468 | 62 | 48 | PASS/COMPLETE | 0, 0 |
| 101 | hub | quick | warm | 30 | 517 / 537 | 156 / 172 | 38 | 48 | PASS/COMPLETE | 48, 48 |
| 101 | hub | full | cold | 30 | 1,387 / 1,479 | 919 / 993 | 76 | 48 | PASS/COMPLETE | 0, 0 |
| 101 | hub | full | warm | 30 | 613 / 631 | 164 / 168 | 41 | 48 | PASS/COMPLETE | 101, 101 |
| 1,001 | leaf | quick | cold | 30 | 1,396 / 1,419 | 1,012 / 1,030 | 81 | 2 | PASS/COMPLETE | 0, 0 |
| 1,001 | leaf | quick | warm | 30 | 1,391 / 1,414 | 1,007 / 1,035 | 81 | 2 | PASS/COMPLETE | 2, 2 |
| 1,001 | leaf | full | cold | 30 | 8,748 / 9,058 | 6,788 / 7,124 | 167 | 2 | PASS/COMPLETE | 0, 0 |
| 1,001 | leaf | full | warm | 30 | 3,086 / 3,158 | 1,162 / 1,179 | 131 | 2 | PASS/COMPLETE | 1001, 1001 |
| 1,001 | hub | quick | cold | 30 | 4,266 / 4,406 | 3,085 / 3,171 | 129 | 570 | INCOMPLETE/INCOMPLETE | 0, 0 |
| 1,001 | hub | quick | warm | 30 | 2,226 / 2,269 | 1,091 / 1,138 | 110 | 570 | PASS/COMPLETE | 570, 570 |
| 1,001 | hub | full | cold | 30 | 8,953 / 9,322 | 6,941 / 7,294 | 168 | 570 | PASS/COMPLETE | 0, 0 |
| 1,001 | hub | full | warm | 30 | 3,122 / 3,350 | 1,175 / 1,233 | 133 | 570 | PASS/COMPLETE | 1001, 1001 |
| 10,001 | leaf | quick | cold | 3 | 12,732 / 12,740 | 11,835 / 11,853 | 380 | 2 | PASS/COMPLETE | 0, 0 |
| 10,001 | leaf | quick | warm | 3 | 12,458 / 15,183 | 11,557 / 14,207 | 380 | 2 | PASS/COMPLETE | 2, 2 |
| 10,001 | leaf | full | cold | 3 | 93,793 / 101,480 | 74,838 / 82,965 | 468 | 2 | PASS/COMPLETE | 0, 0 |
| 10,001 | leaf | full | warm | 3 | 30,942 / 31,891 | 14,758 / 15,292 | 706 | 2 | PASS/COMPLETE | 10001, 10001 |
| 10,001 | hub | quick | cold | 3 | 28,742 / 29,632 | 19,216 / 19,844 | 592 | 5749 | INCOMPLETE/INCOMPLETE | 0, 0 |
| 10,001 | hub | quick | warm | 3 | 25,217 / 25,618 | 15,354 / 15,802 | 586 | 5749 | INCOMPLETE/INCOMPLETE | 4731, 3748 |
| 10,001 | hub | full | cold | 3 | 89,092 / 90,279 | 71,631 / 72,751 | 476 | 5749 | PASS/COMPLETE | 0, 0 |
| 10,001 | hub | full | warm | 3 | 31,963 / 33,594 | 15,522 / 16,359 | 723 | 5749 | PASS/COMPLETE | 10001, 10001 |

Observations from these measurements (not targets):

- **What warm buys depends on the mode.**
  - *Full mode:* warm reuses every summary. Wall p50 fell from 1,374 to 608 ms (about 100 functions), 8,748 to 3,086 ms (about 1,000) and 93,793 to 30,942 ms (about 10,000, leaf change).
  - *Quick mode, small slice:* warm and cold differ little, because the run is dominated by capturing (Git reads) and lowering *both* snapshots in full to compute validity keys. Leaf change: 457 vs 448 ms (100 functions), 1,396 vs 1,391 ms (1,000), 12.7 vs 12.5 s (10,000). This is a disclosed known gap.
- **Highly connected change.** The hub change invalidates 48 of 101, 570 of 1,001 and 5,749 of 10,001 symbols.
  - At 1,000 functions, a cold quick run exceeds `quick_budget_ms = 2000` and reports **INCOMPLETE** (the honest outcome; full mode finishes, PASS). A warm quick run completes from complete cache entries.
  - At 10,000 functions, quick mode is INCOMPLETE both cold and warm. The warm runs hit 4,731 base and 3,748 candidate entries but still exceed the budget.
- **Gap between wall time and engine time in warm full runs** (for example about 16 s at 10,000 functions). A cProfile of one warm full run at about 1,000 functions put about 4.6 s of 7.7 profiled seconds in the CLI's JSON Schema self-validation of the large report (I-004; active only when `jsonschema` is installed). Process start and imports are included in wall time as well.
- **Memory.** Peak RSS p50: 36-77 MB (about 100 functions), 81-167 MB (about 1,000), 380-723 MB (about 10,000). Warm full runs use more than cold at 10,000 because every reused outcome carries its stored evidence record.
- **docs/12's provisional "two-second p95 quick-static" target** is met here only for the about-100- and about-1,000-function leaf changes. It is not met for the 1,000-function hub change (cold), and not at 10,000 functions. The target is not rewritten; these are the measured outcomes for this synthetic setup on this host.

## M2 adversarial checks

- **Random differential for M2 constructs** (`tests/differential/test_m2_differential.py`). 30 generated functions per corpus over declared ints, a declared list, a declared optional int, list literals, `len`/truthiness guards, constant and computed indexes, aliases passed to unknown calls, `is None` guards, and helpers (call summaries) that return None, divide or index. PROVED_SAFE is checked on a 588-input grid; PROVED_UNSAFE witnesses are replayed; via-call claims are matched on the full traceback frame chain. Seeds 7, 8 (solver) and 9 (no solver) run in the suite. **It found one false proof (I-051):** `v is None` on an optional callee result was decided False, so a reachable `1 // 0` was "UNREACHABLE". The fix is a whitelist; the regression test fails with the old rule. After the fix an extra sweep of 80 corpora (seeds 1000-1039, with and without the solver; 2,400 functions) found 0 false PROVED_SAFE and 0 false PROVED_UNSAFE. 3 corpora fell below the per-corpus non-vacuity thresholds, which is a generator distribution issue, not a soundness one.
- **Slice soundness sweeps** (`tests/integration/test_incremental.py`): 100 extra random project seeds (100-199) showed 0 mismatches between quick and full and between out-of-slice results and an independent base check. An earlier sweep under the default 100 ms wall-clock timeout showed 3 load-dependent differences: one query took 97.7 ms in one run and 102 ms in another (UNSAT vs TIMEOUT). That led to I-050 (deterministic rlimit in these tests) and I-041 (per-function Z3 contexts). Both are disclosed below.
- **Security of the Git boundary.** `test_git_reader_uses_argument_arrays_only` pins a single `subprocess.run(argv)` with no shell, `argv` a list starting with the resolved `git` executable, and no checkout/reset/stash/hash-object/filters/textconv strings. References starting with `-` are rejected (`--base --output=x` exits 4).

- **Second finding, from code review (I-052):** instantiated calls evaluated keyword arguments in parameter order, not call-site order. `d(k=1 // a, x=1 // 0)` made `1 // a` unreachable (a false PROVED_SAFE). It is fixed, the regression test fails with the old order, and the differential generator now covers keyword calls.
- **Robustness on real code (no ground truth, not a benchmark).** `guard check <miniconda python3.13 lib incl. site-packages> --root <same>`: 8,333 files, 154,678 requested symbols, 29,784 analyzed. There were 0 engine errors and 0 skipped checks; exit 1 because of the same 2 genuine PROVED_UNSAFE results as M1 (boltons `whoops_math` `1/0`; jinja2 `test_divisibleby`, CONDITIONAL). Division PROVED_SAFE rose from 152 (M1) to 319 thanks to summaries and constants. New M2 obligations: `index.out_of_range` 273 PROVED_SAFE, 349 UNKNOWN, 15 POSSIBLY_UNSAFE; `none.attribute_access` 11 PROVED_SAFE, 24 UNKNOWN, 9 POSSIBLY_UNSAFE; 7,941 advisory via-call obligations. Cost (`/usr/bin/time -l`, one run each):
  - first M2 build: 311 s wall, 4.78 GB peak RSS;
  - after I-053: 220 s wall, 2.26 GB peak RSS, with all 34,724 obligation results and witnesses identical;
  - M1 for comparison (measured during the M1 audit): 134 s.

  The remaining difference is real added work. Call summaries grow the IR to 1.21 M instructions (mean 40.5 per function, p99 374, max 6,293), and lowering is now about 95% of the run.

## Audit (M0/M1, adversarial review)

An adversarial audit of M0/M1 was run after the builder's gate report. It used manual source review, 40 hand-written probe programs, and a seeded random differential generator (now `tests/differential/test_random_differential.py`). The generator produces M1-subset functions, analyzes them with the real pipeline, and replays every claim in an isolated `python -I` / `python -I -O` subprocess at the exact source span. Each finding was confirmed by a concrete counterexample before it was fixed. Each fix has a regression test in `tests/integration/test_audit_regressions.py`, and each of those tests was shown to fail when its fix was temporarily reverted.

| ID | Severity | Finding (confirmed) | Fix | Regression test |
| --- | --- | --- | --- | --- |
| A-01 | critical | **False PROVED_SAFE.** Z3 constants for parameters used the raw Python name. A parameter named like an internal constant (`floordiv_q_v3`) aliased the floor-division quotient, so `q = b // 2; 1 / (q - floordiv_q_v3 + 1)` was PROVED_SAFE EXACT_MODEL. CPython raises ZeroDivisionError at `(floordiv_q_v3=1, b=0)`. | Parameters are encoded as `param!<name>` (I-024). | `test_a01_parameter_named_like_internal_solver_constant` |
| A-07 | critical | **False PROVED_SAFE.** After `x / d` with `x` of unmodeled type, both the interval analysis and the Z3 prefix assumed `d != 0`. `1 / d` was then PROVED_SAFE. Its only assumption was about frame introspection, which does not cover the case. CPython fails at the second division with `x = X()` (where `__truediv__` returns 0) and `d = 0`. | Divisions with the `python.dynamic_dispatch` flavor refine nothing. The cone includes both operands of earlier divisions (I-029). | `test_a07_dynamic_dispatch_division_does_not_refine_divisor` |
| A-02 | major | **Engine hang.** `x = x * x` repeated 40 times made interval arithmetic and the evaluator compute 10^(2^40). `guard check` did not finish within 60 s (killed), because interval analysis is not budget-gated. | Sound clamping of interval bounds at 2^256 (widening only). The evaluator refuses multiplications over 2^16 bits (I-025). The fixture now finishes and is PROVED_SAFE. | `test_a02_repeated_squaring_terminates_and_stays_sound`, `test_a02_interval_clamping_only_widens` |
| A-03 | major | **IR evaluator disagreed with CPython.** It raised UnboundLocalError when it evaluated a maybe-unbound PHI, even if the variable was never read. Genuine witnesses were rejected. In the first fuzz runs (seeds 1-6, 60 functions each) there were 3-18 POSSIBLY_UNSAFE per run. The inspected cases were real ZeroDivisionErrors reported with the misleading reason "UnboundLocalError". After the fix, seeds 11-13 had 0 POSSIBLY_UNSAFE. A naive lazy fix would have produced false PROVED_UNSAFE, because a plain alias `x = d` emitted no IR instruction. | Checked-read `ASSIGN` at every read of a maybe-unbound value, lazy UNBOUND in the evaluator, and boundness encoded in the Z3 prefix (I-026). | `test_a03_*` (4 tests) |
| A-04 | major | **Coverage honesty.** In-scope `.py` files that were symlinks, larger than 5 MiB or unreadable were silently dropped, and the run could PASS. `guard check notes.txt` returned PASS (exit 0) with nothing analyzed. With `require_coverage = false`, a syntax-error file gave PASS without disclosure. | Required `excluded_path` skipped checks, so the run is INCOMPLETE (exit 2). Non-required skips give WARN. A PASS over zero files is labelled vacuous (I-027). | `test_a04_*` (4 tests) |
| A-05 | major | **Crash on a legal filename.** A file named `a:b.py` produced a report that violated the schema (the symbol-ID separator and the drive-letter rule), so the run exited 3 with no JSON. | Such paths are required `excluded_path` skips with a schema-valid report (exit 2). | `test_a05_path_with_colon_is_skipped_with_schema_valid_json` |
| A-06 | major | **Schema accepted contradictory labels.** It accepted PROVED_SAFE + OVERAPPROXIMATE/BOUNDED, POSSIBLY_UNSAFE + EXACT_MODEL, UNKNOWN with a scope, and `patch_relation = INTRODUCED` in a `guard check` report. | `common.$defs.result_scope_rules` is referenced by the obligation_result and finding schemas, and `check` reports require UNDETERMINED (I-028). | `test_a06_schema_rejects_contradictory_result_scope_pairs` |
| A-08 | minor | **Scope over-claim.** A `float`-annotated dividend was trusted as a built-in float while the dependent proof stayed EXACT_MODEL. A CONDITIONAL proof that depended on unmodeled inputs had an empty assumption list. | Annotation-derived int/bool/float parameters in the cone make the result CONDITIONAL with a stated assumption. CONDITIONAL always lists its assumptions (I-030). | `test_a08_float_annotation_in_cone_is_conditional`, `test_a08_conditional_results_always_state_assumptions` |

**New adversarial coverage.** `tests/integration/test_audit_regressions.py` has 50 tests. 30 parametrized edge cases each carry an expected result and scope, and every PROVED_UNSAFE witness is replayed in CPython. Cases include `d = a - a`; `a > 0 and a < 1`; nested branches; reassignment after a guard; division by a bool (annotation and literal); `True + True - 2`; `x // -1`; floor and modulo sign; walrus; global mutation and read; joins that must drop one-branch facts; `not`/chained guards; assert under `-O`; code after `raise`; `is None` on a declared int; loops; `try`; unknown-call dividends; `-0.0` and `0.0` literals; and unicode names. Also covered: UTF-8 byte columns, a syntax error that still lets other files be analyzed, a **repository-not-mutated** test (the file tree, modes, sizes, mtimes and bytes are identical after `guard check` in both formats, with no `__pycache__` and no `.guard` state), and a **static scan of the engine source** for `eval`/`exec`/`compile`/`__import__`, subprocess/runpy/socket/pickle/ctypes imports and dynamic imports other than `z3`. `tests/differential/test_random_differential.py` runs 3 seeded corpora of 40 functions each, with and without the solver, and requires at least 50 PROVED_SAFE and 20 PROVED_UNSAFE per corpus so it cannot pass vacuously. Mutation check: with an off-by-one injected in LT refinement, or with nonzero kept on join, the generator test fails. A sound weakening (GT refined as GE) passes, as it should. Additional sweep: seeds 1000-1011, each with and without the solver (24 corpora, about 960 functions), gave 0 false PROVED_SAFE and 0 false PROVED_UNSAFE.

**Post-audit robustness rerun** (same corpus as the smoke run below, `guard check <miniconda python3.13 lib> --root <same>`): 8,333 files, 0 engine errors, 0 skipped checks. 29,784 of 154,678 symbols were analyzed, with obligation counts identical to the pre-audit run (152 PROVED_SAFE, 2 PROVED_UNSAFE, 2 POSSIBLY_UNSAFE, 25,946 UNKNOWN). Wall time was 134 s (`/usr/bin/time -p`), against 78 s before the audit. That is a single unrepeated measurement on a shared machine; the clamping in `Interval.__post_init__` and the checked reads add per-operation overhead. It was not profiled and is not a benchmark.

**Checked and found correct (no change).** No hard-coded demo findings, witnesses or timings in `src/`; mocks in tests only inject engine faults. The verdict precedence is ERROR > BLOCK > INCOMPLETE > WARN > PASS, and exit codes 0/1/2/3/4 match docs/09. TESTED/bounded witness search never yields PROVED_SAFE. Annotation-only int domains are CONDITIONAL. SAT on a query with havoc, or with an unvalidated witness, gives POSSIBLY_UNSAFE OVERAPPROXIMATE. A solver TIMEOUT or RESOURCE_LIMIT is UNKNOWN. ERROR (engine fault) is distinct from INCOMPLETE. The existing T10 sentinel never imports target code. Process calls exist only in tests and use argument arrays. Schema validation uses real jsonschema and rejects unknown enums. The demo results before and after the audit are identical (verdict BLOCK, run_outcome INCOMPLETE; 6 PROVED_UNSAFE, 7 PROVED_SAFE, 1 POSSIBLY_UNSAFE, 10 UNKNOWN). Only assumption lists on UNKNOWN results gained text.

**Observations recorded, not changed (policy interpretation or precision only).**
- A division after an unsupported loop or an unknown call, with a divisor that is exactly 0 (for example `while True: pass` then `1 / 0`), is POSSIBLY_UNSAFE OVERAPPROXIMATE (WARN, counted as resolved), not UNKNOWN. This follows docs/03 ("a bad state is not excluded by an approximate model or incomplete path"). The obligation's divisor does not depend on the unknown construct; only reachability does. If maintainers want reachability-only taint to count as INCOMPLETE, this is a one-line policy change.
- `"fmt" % args` (string formatting) is a syntactic `%` and stays an UNKNOWN obligation, so real code that formats with `%` is INCOMPLETE. This is honest but imprecise; a str-receiver rule is a candidate capability expansion.
- `a / -0.0` is UNKNOWN (unary minus on float is not modeled), although CPython raises. This is imprecise, not unsound.
- Equality between an int and a str or float literal (`a == 'x'`) is havoc in the solver, which gives POSSIBLY_UNSAFE where the branch is actually infeasible. This is imprecise, not unsound.

## M5 repair loop (recorded run)

Command: `.venv/bin/python examples/demo_repo/run_demo.py --out examples/demo_repo/recorded_run` (exit 0; 173.1 s total including 99.4 s of discovery). Host: the reference host above, with Docker 29.5.2 and Ollama 0.35.1 serving qwen2.5-coder:7b at `sha256:dae161e2…4364`. All values are read from the 36 files in `examples/demo_repo/recorded_run/` (written once, unedited). **The agent client is the local-model harness in `run_demo.py`, which talks to `guard mcp serve --stdio` through the official MCP SDK client; it is not Claude Code.**

1. Baseline commit `b40ba95f…`: safe `ratio` and `normalize`, the declared ratio domain, the isolated profile `isolated-python`, and the declared contract `normalize.sum.v1`. `guard runner probe`: AVAILABLE, with all six capabilities VERIFIED. `guard baseline create --rev`: exit 0.
2. Candidate (unguarded `ratio`, `sum(xs) + 1.0`). `guard verify --execute`: BLOCK, exit 1, 5.6 s.
   - `ratio`: INTRODUCED PROVED_UNSAFE [EXACT_MODEL], validated witness `{a: 0, b: 0}`. docs/13 names a=1, b=1; any a == b is a witness and the solver returned 0, 0.
   - `normalize.sum.v1`: OBSERVED_REGRESSION INTRODUCED. First witness `[1.0, 1.0]`, minimized to `xs=[1]`: base returns `[1.0]` (predicate holds), candidate returns `[0.5]` (fails, and again on repeat). Docker environment digest `sha256:66056bd1…`.
   - `normalize`'s own division: UNKNOWN (list comprehension unsupported), INFO.
3. MCP `verify_patch` (same candidate snapshot `sha256:05008e6f…`): BLOCK. Compact response about 6.0 KB versus a 63.4 KB full report. For both ERROR findings the harness called `get_contracts`, `get_finding` and `explain_finding`. The contract finding's `get_finding` carries the checked revision's precondition and predicate, plus base and candidate EXECUTION evidence.
4. Attempt 1. The model was asked once per function, at temperature 0 with schema-constrained JSON. Both replacements were validated and applied:
   - `normalize`: `total = sum(xs) + 1.0` became `total = sum(xs)`. 18.3 s, 113 output tokens.
   - `ratio`: a `d == 0` guard was added, but it returns **None** where the baseline returned 0.0, with a comment "or some other appropriate value or handling". 25.2 s, 144 output tokens.
5. MCP `verify_patch` on the repaired tree: PASS, exit 0, snapshot `sha256:66d3969c…`. `ratio` is PROVED_SAFE [EXACT_MODEL], and the contract is TESTED (finite probes). After this, `get_finding` on both pre-repair findings returns STALE_SNAPSHOT (`evidence_status` STALE, `superseded_by_run` = the PASS run).
6. Final CLI `guard verify --execute`: PASS, exit 0, the same snapshot `sha256:66d3969c…`, 4.9 s. Verdict reasons: one unaffected symbol (the restored `normalize`) was outside the quick slice; one contract TESTED on finite probes (not a proof); all required obligations resolved.
7. No-model static run (`guard check demo`): INCOMPLETE, exit 2, 0.4 s. The required division in `normalize` is UNKNOWN ("ListComp expression is outside the supported subset"). This is honest: unsupported work is never PASS.
8. Local-model discovery (`--execute --dry-run`, nothing recorded): 99.4 s; the model answered for `ratio` in 93.9 s (578 output tokens).
   - Model candidates: 2 rejected as VACUOUS_PREDICATE (`unchanged_argument` on int arguments), and 1 REFUTED (OBSERVED_FAILURE: "result == 100", falsified in Docker).
   - Template candidates: repeatability TESTED on 48 inputs.
   - `normalize` has no input profile without annotations, so it is not a discovery target.

Latency (agent wall clock; definitions in `examples/demo_repo/README.md`):

| Measure | Value |
| --- | --- |
| Loop: first failing `verify_patch` result to final `verify_patch` result | 50.2 s |
| Model requests in the loop (2) | 43.5 s (18.3 + 25.2) |
| Verifier in the loop (`verifier_wall_ms`, 1 call) | 6.2 s; round trip 6.3 s |
| Evidence fetches (get_contracts + 2x get_finding + 2x explain_finding) | 0.39 s |
| Other harness time | 0.04 s |
| Verifier on the candidate (MCP, before the loop) | 6.8 s (round trip 7.0 s); CLI equivalent 5.6 s |

What this does and does not show:
- It shows a real patch, a real BLOCK with a static witness and a replayed regression, model-proposed repairs applied only in a temporary repository, and a re-verified final snapshot with stale pre-repair evidence flagged.
- It does not show that the repairs preserve intent beyond the declared properties. The `ratio` repair changes the a == b result from 0.0 to None. No contract covers `ratio`'s return value, so the verifier has nothing to object to; a reviewer would. Model answers are not deterministic at temperature 0 (M4).
- One loop on two small fixtures is not an effectiveness measurement.

Trial runs before the recording (scratch directories, not kept as evidence) exposed two problems:
- **Missing contract definition.** The first trial's prompt lacked the contract's predicate, because a report carries only contract IDs and digests. The model then failed to fix `normalize` in 3 attempts: it filtered the list, returned `xs` when the total is 0, then repeated an identical function, which was rejected. This led to `get_finding` returning the checked contract revision (I-096/I-099); with it, the model fixed `normalize` in attempt 1 in both later runs.
- **Runner unavailable under load.** In the second trial the MCP re-verify reported RUNNER_UNAVAILABLE ("docker daemon not available: timed out", a 20 s probe under host load). The verdict was INCOMPLETE (exit 2) and there was no fallback. The final CLI verify of the same snapshot minutes later was PASS. In that trial, one `verify_patch` round trip was 45.9 s for a 29.1 s server handler. The cause of the 16.8 s difference was not determined; other calls showed under 1 s of transport overhead.

A first complete recording at 16:56Z had the same outcome: one attempt, PASS, and the same repairs. It was replaced, not edited, by the current recording (17:01Z), whose only difference is the added post-repair `get_finding` staleness step.

## M5 adversarial checks

| Attack or failure | Expected | Observed (test) |
| --- | --- | --- |
| Stray `print`, `sys.__stdout__` write, or child process writing to fd 1 during a tool call | never on the wire | only on stderr; every stdout line is a frame (`test_stdout_carries_only_protocol_frames`) |
| Buffered stdout flushed at exit | not on the wire | prototype observed the corruption; fixed by rebinding and flushing before fd 1 is restored (I-091) |
| `repository` = another directory, a subdirectory, or the parent | NOT_AUTHORIZED | NOT_AUTHORIZED (`test_only_configured_roots_and_inside_paths_are_served`) |
| `path` = `../other`, a symlink out of the root, or an absolute path | NOT_AUTHORIZED / INVALID_REQUEST | as expected (same test) |
| `base` = `--output=/tmp/x` | refused before Git | INVALID_REQUEST |
| Unknown tool (`approve_contract`) or extra argument | refused | INVALID_REQUEST (service and MCP) |
| Repository edits a preauthorized profile (memory 256 to 4096 MB) | not authorized | NOT_AUTHORIZED "changed since the server started" |
| Unisolated profile passed with `--allow-profile` | not authorized | NOT_AUTHORIZED "not isolated" |
| Config in a parent directory of a non-Git root | ignored | ignored: CONDITIONAL rather than EXACT_MODEL (`test_config_is_read_only_from_the_root_never_a_parent`) |
| Client cancels mid-run | run failed, nothing published, server alive | as expected (`test_client_cancellation_finalizes_the_run_as_failed`) |
| Server timeout | explicit TIMEOUT, nothing published | as expected; the next run has 0 cache hits |
| Edits during both attempts | STALE_SNAPSHOT, nothing cached | as expected |
| Fetch a pre-repair finding after the repair | STALE, not attributed to the new code | STALE_SNAPSHOT + `superseded_by_run` (test and recorded run) |
| `finding_id` = `../../etc/passwd` | refused | INVALID_REQUEST |
| Deleting indexed reports | refused | SQLite trigger aborts ("append-only") |
| Network from the server process | none | socket connect, bind and listen booby-trapped; nothing attempted (`test_server_process_opens_no_network_connection`) |

## M4 measurements

All numbers below come from real runs on the host in the table above. Inference used qwen2.5-coder:7b via Ollama 0.35.1 on the Apple M4 GPU; falsification ran in Docker containers. The host was under heavy unrelated load during the evaluation: Chrome, the Docker Desktop VM, and system daemons (ReportCrash, Spotlight, sandboxd). `uptime` load averages were 96.9 (1 min) at 10:55, about 10-11 at 11:13-11:54, and 5.3 at 12:07 local time; memory free was 21-23% (`memory_pressure`). Latencies are therefore pessimistic for this hardware and noisy.

**Evaluation (T44)**
- Command: `.venv/bin/python benchmarks/model_eval.py --repeats 3 --template-repeats 3 --out benchmarks/results/m4_model_eval.json`. Run 2026-10-08 14:12:29Z to 15:54:17Z, engine 0.3.1, prompt `ig-contract-prompt-1` (system prompt digest `sha256:3618decc...`), temperature 0, seed 0, `max_probes` 32, request timeout 900 s.
- Held-out set: `benchmarks/manifests/m4_heldout.json`, 13 functions, 5 seeded bugs, 0 overlap with the prompt.
- An earlier run was stopped after one model repeat because its scoring was wrong (the generic oracle credited a seeded bug to a claim refuted only by a type mismatch). Its partial output was discarded, not published. The scorer now judges each refuted claim with the DSL evaluator against the intended behavior on 33 inputs. The model answers in that stopped repeat produced the same counts as repeat 0 below.

| Metric (per repeat; denominator = raw candidates of that source) | no model, no templates | templates (3 runs) | local model (3 runs) |
| --- | --- | --- | --- |
| raw candidates | 0 | 23, 23, 23 | 35, 35, 34 |
| schema-valid rate (strict schema + DSL typing/symbols) | n/a | 1.00 | 0.800, 0.829, 0.824 (mean 0.817, sd 0.015) |
| non-vacuous rate (also admissible precondition + falsifiable predicate) | n/a | 1.00 | 0.657, 0.657, 0.676 (mean 0.664, sd 0.011) |
| mechanically checkable rate (decidable falsification: TESTED or REFUTED) | n/a | 1.00 | 0.657, 0.657, 0.676 (every non-vacuous candidate was decided) |
| rejection codes | none | none | TYPE_ERROR 7/6/6, VACUOUS_PREDICATE 2/2/3, DUPLICATE (in-response) 3/4/2; no malformed answers, no FORBIDDEN_FIELD, no source-reference errors |
| validated, same claim as a template (de-duplicated) | n/a | n/a | 12, 10, 12 |
| distinct new claims beyond templates | n/a | 23 (all claims) | 11, 13, 11 |
| TESTED / REFUTED / UNKNOWN (distinct) | 0 / 0 / 0 | 23 / 0 / 0 | 1/10/0, 1/12/0, 1/10/0 |
| refuted claims valid for the intended behavior (33-input check) | 0 | 0 | 2, 3, 2 |
| false claims caught by falsification | 0 | 0 | 8, 9, 8 (of which DSL type mismatch 1, 2, 2) |
| seeded bugs exposed (of 5) | 0 | 0, 0, 0 | 2, 3, 2: `clip_negatives` and `dedupe_sorted` every run; `count_positive` in run 2 |
| wall time, whole discover run | 0 | 36.0, 40.6, 39.8 s | 1,255, 1,277, 1,116 s |
| model time per function (13 functions) | n/a | n/a | mean 91.8 / 93.6 / 81.6 s (min 30.1 s, max 130.2 s) |
| model time per raw candidate | n/a | n/a | 34.1, 34.8, 31.2 s |
| falsification time (Docker), all candidates | n/a | 33.4, 38.3, 37.2 s | 31.6, 30.6, 33.2 s |
| tokens per function (prompt / output) | n/a | n/a | about 1,430 / 470-502 (mean); all `done_reason: stop` (no truncation) |
| generation speed | n/a | n/a | mean 5.5 / 5.9 / 6.6 output tok/s (one function at 0.18 tok/s during the load-96 period) |
| peak RSS | guard process 38.3 MB | (same process) | inference server (`llama-server`) 4,901-4,906 MB, sampled every 0.5 s |

**Findings**
- **Determinism.** The answers are not reproducible at temperature 0 with a fixed seed: 10 of 13 response digests differed between repeats 0 and 1 (`identical_responses_across_repeats: false`). The variance above is the runtime's, not the sampler's.
- **Value over templates.** The 23 template claims (purity, length, repeatability) were all TESTED and exposed none of the 5 seeded bugs. Those templates describe shape, not intent. The model's docstring-derived claims exposed 2-3 of them through mechanical falsification:
  - `clip_negatives`: "all results in [0, 1000]", counterexample `[-1]`;
  - `dedupe_sorted`: "does not modify xs", counterexample `[0, -1]`;
  - `count_positive`: "for non-positive inputs the result is 0", counterexample `[0]`.
- **Missed bugs.** `normalize_to_unit`'s claim "sum(result) approx 1" lacked a positive-sum precondition, so it fails for the intended behavior too and was judged false. `clamp_percent`'s claim applied a list operator to a float (type mismatch).
- **False claims.** Most model claims (8-9 per run) were wrong about the code. Falsification refuted every one of them, and none was recorded. Without `--execute` such claims would be recorded as INFERRED (advisory, WARN-only) candidates; this is why falsification is recommended with `--model local`.
- **Time.** Model latency dominates on this host: ~34 s per raw candidate versus about 1.5 s (templates) to 3 s (model claims, including shrinking and confirmation) of Docker falsification per falsified candidate. Discovery is an explicit command, never part of `guard verify`.
- **Usefulness (developer review)** was not measured. No independent maintainer reviewed the claims; the 33-input intended-behavior check is a mechanical proxy, not intent review (docs/08).

**Real-model CLI demonstration (T42).** A fresh copy of the held-out set with the Docker profile:
- `guard model status`: exit 0, digest `sha256:dae161e2...4364`, `ollama 0.35.1`, Q4_K_M, `apache-2.0 (Apache License; license text sha256:832dd9e0...)`, `json_schema`, operations `["propose_contracts"]`, probe 86 ms.
- `guard contracts discover --model local --execute --symbol heldout/sequences.py::clip_negatives`: exit 0, COMPLETE, 82.5 s wall (model 75.8 s for 1,385 prompt / 522 output tokens; context: target only, 544 bytes; falsification 4.4 s).
  - The 3 template claims were TESTED and recorded INFERRED.
  - The model's 3 claims: 2 duplicated templates; `all_between(result, 0, 1000)` was REFUTED with counterexample `[-1]` (minimized from `[-1, 1000]`, confirmed in a fresh container) and not recorded.
  - `guard contracts list` shows only the 3 INFERRED template contracts with no evidence.
- `IG_REAL_MODEL=1 .venv/bin/python -m pytest -q tests/integration/test_real_model.py`: 1 passed in 89.25 s (real model, unisolated profile).

## M3 measurements

Measured with `.venv/bin/python benchmarks/contract_timing.py --reps 10` (raw data: `benchmarks/results/m3_contracts_20261004_170610.json`). Same host as M2, Docker Desktop 29.5.2, image as in the versions table.

- Scenario: Fixture B with one DECLARED contract. *regression* is the patched normalize (batch probes, shrinking, fresh-run confirmation). *tested* is the repaired candidate (batch probes only).
- n = 10 fresh child processes per condition.
- `dynamic` is the report's `timing_ms.dynamic`; `probe` is `timing_ms.runner_probe` (the backend availability and capability check, once per command, before any import).
- A first run of this script, made before `runner_probe` was reported separately, was discarded. Its Docker wall p50s were 4,731 ms (regression) and 3,217 ms (tested).

| backend | scenario | runner invocations | wall p50 / p95 (ms) | dynamic p50 / p95 (ms) | probe p50 (ms) | outcome (all runs) |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| docker | regression | 9 | 4,280 / 5,349 | 3,032 / 4,120 | 635 | BLOCK, OBSERVED_REGRESSION |
| docker | tested | 2 | 2,406 / 2,941 | 1,223 / 1,432 | 648 | PASS, TESTED |
| unisolated | regression | 9 | 1,110 / 1,213 | 688 / 779 | 5 | BLOCK, OBSERVED_REGRESSION |
| unisolated | tested | 2 | 783 / 947 | 386 / 498 | 5 | PASS, TESTED |

Observations (not targets):
- A container run costs about 0.45 s on this host.
- A confirmed regression needs 9 runner invocations: 2 batch, 2x2 shrink rounds and 3 confirmation. Base and candidate batches run in parallel.
- Dynamic checks are explicit (`--execute`) and are not part of the quick-static budget.

## M3 adversarial checks

- **Fault injection (each must make a test fail; sources restored byte-identical afterwards):**
  - shrinking without the precondition re-check fails `test_shrinking_preserves_the_contract_domain`;
  - classifying both-fail as regression fails `test_both_versions_fail_is_pre_existing_not_a_regression`;
  - treating an unverified capability as verified fails `test_failed_capability_probe_fails_closed_before_any_import`.
- **Found and fixed during M3:**
  - a target that forks kept the result pipe open, so the harness waited until the wall limit. Observations are now length-prefixed frames (`test_resource_limits_and_timeouts_are_runner_outcomes`);
  - contract evidence was written before the snapshot consistency check. It is now recorded only for consistent runs (`test_evidence_is_recorded_only_for_consistent_runs`);
  - `require_coverage = false` produced an empty `required_contracts` plan while results still said required. Now consistent (`test_without_required_coverage_an_undecided_contract_is_advisory`);
  - the dynamic phase counted against the static quick budget and made static results look budget-limited. The static budget is now read before dynamic checks;
  - the blocking reason for a contract regression used the static wording. There is now a separate "blocking contract regression" reason.
- **Static scan of the engine** (`test_engine_source_has_no_execution_primitives`, extended):
  - only `snapshots/git.py` and `execution/process.py` import `subprocess`;
  - `process.py` uses one `Popen(argv, env=env, start_new_session=True)` without a shell (`test_execution_process_uses_argument_arrays_only`);
  - `probe_harness.py` is never imported by the engine (`test_probe_harness_is_never_imported_by_the_engine`). It is the only file allowed `import_module`/`fork`/`socket`, because it runs inside the boundary.
- **Two pre-M3 test expectations were updated because the behavior changed by design.** `test_capabilities_reports_unimplemented_features_honestly` now expects execution "available (explicit)" and still expects model/MCP unavailable. `test_invalid_config_rejected` no longer treats `[execution] enabled = true` as invalid; six new invalid execution configurations were added in its place. No test was deleted or weakened.

## Audit (M2/M3)

An adversarial audit of M2 (snapshots, impact, baselines) and M3 (contracts, isolated replay) was run after the builder's gate report. Methods used:
- about 110 hand-written probe programs, each run through `guard check`/`guard verify` and compared against an exhaustive CPython ground truth: every input of a small domain, run in a separate interpreter;
- my own container probes, built from the runner's own `docker run` argv but with my own script inside;
- source review of the evaluator, lowering, attribution, runner, harness, DSL and checker;
- runs of every example.

Each finding below was confirmed by a concrete counterexample before it was fixed. Each fix has a regression test, and each test was shown to fail with its fix temporarily reverted (A2-03 is a version bump and has no fault-injection test). The reverts were made from scratch backups; nothing was committed or stashed.

| ID | Severity | Finding (confirmed) | Fix | Regression test |
| --- | --- | --- | --- | --- |
| A2-01 | critical | **False PROVED_UNSAFE EXACT_MODEL (BLOCK).** The IR evaluator evaluated `ys *= 0` as `ys * 0` (a fresh list), so an alias `xs` kept its old contents. The bounded witness search then "validated" `xs=[0]` for `ys = xs; ys *= 0; if len(xs) > 0: return 10 // 0`, but CPython never fails there (`xs` is always empty). The same happened for `ys += [1]` with `if len(xs) == 0`. | Augmented operations use the in-place protocol (`operator.iadd`/`imul`/...). PARAM values are deep-copied, so validation never mutates the reported witness (I-072). Both cases are now POSSIBLY_UNSAFE (WARN). | `tests/differential/test_audit_m2m3_adversarial.py::…[alias_inplace_repeat]`, `[alias_inplace_concat]`; `tests/integration/test_audit_m2m3_boundaries.py::test_evaluator_uses_inplace_protocol_and_never_mutates_the_witness` |
| A2-02 | major | **Silently dropped obligations (coverage honesty).** `list + list`, `seq * int` and their augmented forms gave an untyped object, so later subscripts stopped being `index.out_of_range` obligations. `xs *= 0; return xs[0]`, `ys = xs + [1]; return ys[1]` and `ys = [1] * len(xs); return ys[0]` were PASS with **0 obligations** (exit 0), although CPython raises IndexError for `xs = []`. | Exact sequence results for built-in concatenation and repetition, with lengths in intervals and Z3. An augmented list operation is an UNKNOWN effect, because it mutates every alias (I-073). The three cases are now UNKNOWN (INCOMPLETE), PROVED_UNSAFE (BLOCK, witness `xs=[]` replayed in CPython) and PROVED_UNSAFE (BLOCK); `xs + [1]` then `[0]` is PROVED_SAFE. | `test_audit_m2m3_adversarial.py::…[inplace_repeat_then_index]`, `[concat_then_index_ok]`, `[concat_then_index_bad]`, `[repeat_then_index_bad]` |
| A2-03 | major | **Stale cache across a semantics change.** Validity keys contain AST fingerprints and the engine version, not IR digests. Without a version bump, a warm `.guard/cache` would have reused results computed under the unsound A2-01/A2-02 semantics. | Engine 0.3.1, semantics profile `…-builtin-v3` (I-074). | Covered by the key tests; the version is part of `global_key_material`. |
| A3-01 | major | **Hostile target crashes the engine.** Runner output is untrusted: target code can write its own frame to the probe pipe. A deeply nested JSON document escaped `_parse_response` as an uncaught `RecursionError` (reproduced: depth 200,000). Inside the harness a forged deep frame aborted the whole probe batch. | Rejected as a runner error on the host, and as a `crashed` probe in the harness (I-075). | `test_audit_m2m3_boundaries.py::test_deeply_nested_runner_output_is_a_runner_error_not_a_crash`, `::test_harness_turns_a_forged_deep_frame_into_a_crashed_probe` (runs the shipped harness on a forging target) |
| A3-02 | critical | **False OBSERVED_REGRESSION (BLOCK) of a DECLARED contract.** In the contract DSL a type mismatch escaped past a decisive sibling, so `or(eq(result, None), lt(result, 5))` was violated by `None`. End to end (unisolated backend): base `return 1`, candidate returning `None` for odd inputs. The contract holds for both, yet the result was OBSERVED_REGRESSION. | Kleene `and`/`or`: a decisive operand wins. Otherwise an undecided operand gives UNKNOWN, and only then does a mismatch propagate (I-076). The end-to-end case is now TESTED with 0 candidate failures. | `test_audit_m2m3_boundaries.py::test_permitted_none_result_is_not_a_contract_regression`, `::test_dsl_decisive_operand_wins_over_type_mismatch`, `::test_dsl_mismatch_with_undecided_sibling_is_unknown` |
| A3-03 | minor | **Environment outcome counted as a violation.** A `MemoryError` under the runner's memory cap made a probe FAIL. A patch that only allocates more could become an OBSERVED_REGRESSION. | UNKNOWN `resource_limit` observation (I-077). | `test_audit_m2m3_boundaries.py::test_memory_error_under_the_runner_cap_is_unknown_not_a_violation` |

**New adversarial tests (40).**
- `tests/differential/test_audit_m2m3_adversarial.py` has 26 cases, each with an expected label and verdict, checked against an exhaustive CPython ground truth run in a `python -I` child. PROVED_SAFE must never fail in CPython at its line, and every PROVED_UNSAFE witness is replayed (with `-O` when the result says assertions are disabled; that result must be CONDITIONAL). Cases:
  - integer semantics: `d = a - a`; `a > 0 and a < 1`; nested branches; reassignment after a guard; joins that must drop one-branch facts; division by a bool comparison; `(a > 0) - (a > 0)`; `x // -1`; `(-7) // 2`; `a % -3`; walrus and global mutation (UNKNOWN); assert under `-O`; loops; unicode identifiers;
  - lists and None: negative indexes; the A2 alias and sequence cases; `ys = xs; ys.clear()`; None reassigned after a guard; None joined from branches.
- `tests/integration/test_audit_m2m3_incremental.py` adds 9 incremental-vs-full change cases. Each changes only a module-level fact a dependent relies on, never the dependent's text:
  - a `global` declaration elsewhere in the callee's module; `globals()` use; a decorator on the callee; rebinding the callee name; a changed default argument (through `from util import denom as dn`); a star import; a package `util/__init__.py` shadowing `util.py`; a constant set to 0; a conditional rebinding.
  - Quick equals full, warm equals cold, and outside-slice results equal an independent base check. The dependent is invalidated in every case. Its results were inspected: UNKNOWN for unresolvable names; INTRODUCED/CHANGED for the default-argument and constant changes.
- `tests/integration/test_audit_m2m3_boundaries.py` (12 tests) adds the A3 tests above. It also adds a **static-boundary sentinel for `guard verify` quick and full, and for `guard contracts discover`**: the base and candidate modules write an absolute marker file and `SystemExit` at import. Neither marker appears, and the patch is still reported as an INTRODUCED PROVED_UNSAFE.

**Isolation claims probed independently.** I ran a container with exactly the runner's argv (`DockerBackend.run_argv`; image `python:3.13-slim`, digest `sha256:3dd7cc10…`) and my own script. Observed:
- network: TCP to 1.1.1.1 and to the Docker host gateway gives ENETUNREACH; UDP send gives ENETUNREACH; DNS and `host.docker.internal` fail; no `/var/run/docker.sock`;
- writes: `/work/src` and `/work/harness` are read-only (EROFS, also chmod), `/` is read-only, `/etc/passwd` gives EACCES; `/tmp` is writable but noexec (EACCES on exec);
- privileges: uid/gid 65534; `mknod` and `setuid(0)` give EPERM;
- environment: a caller secret exported on the host is absent; the environment holds only the image's variables plus HOME, LANG and PYTHONHASHSEED;
- limits: `pids.max` = 32 (fork stopped after 31); `memory.max` = 256 MiB (allocating 2 GiB killed the container, exit 137); RLIMIT_CPU = 10 s;
- no `/Users` or `/host_mnt`; the host source copy was unchanged afterwards, and no `ig-*` containers or workspaces were left behind.

The capability claims hold as stated, for this Docker Desktop VM.

**Checked and found correct (no change).**
- Verdict precedence and exit codes: 0/1/2/3/4, checked for a missing path, a syntax-error file, an unknown flag, verify without `--base`, and PASS.
- `guard check` puts JSON only on stdout.
- Schema validation is real jsonschema (Draft 2020-12). It rejects an unknown `property_result`, `verdict` or `scope_validity` and a missing `assumptions`.
- Coverage honesty: methods, static methods, async functions, nested functions, lambdas, generators, conditional defs and module-level code all stay in the denominator as UNKNOWN (9 requested, 1 analyzed, INCOMPLETE).
- Module-constant resolution rejects every module-level rebinding form tried: walrus (also inside a comprehension), del, augmented assignment, for, with, except-as, import-as, class, match capture, tuple and starred unpacking, annotated assignment, later def, `global` in a class or nested function, `type` alias, `globals()[...]`, and `if __name__ == '__main__'`.
- Patch attribution: a widened guard (`a > 5` → `a > 3`) is CHANGED with witness `a=4`, outside the old domain; a narrowed guard is PRE_EXISTING; a moved root is INTRODUCED plus RESOLVED; `a == 0 or a == 1` is CHANGED.
- UTF-8 byte columns and byte offsets are correct with multi-byte identifiers and emoji.
- No hard-coded demo findings or witnesses in `src/`. The only `eval`/`exec`/`subprocess` uses are `ast.literal_eval` for doctest literals (exceptions caught), `snapshots/git.py` and `execution/process.py` (argument arrays, no shell), and the harness's import of target code inside the runner.
- The Docker tests assert observed target behavior (ENETUNREACH, EROFS, absent secrets, pids and memory limits), not only the harness's self-reported flags.
- Examples, rerun after the fixes:
  - `guard check examples/python_demo` exits 1 (BLOCK/INCOMPLETE; 31 obligations: 10 PROVED_UNSAFE, 9 PROVED_SAFE, 1 POSSIBLY_UNSAFE, 11 UNKNOWN), identical to the M3 report. baseline/candidate/unknown exit 0/1/2.
  - `examples/patch_demo/run_demo.py`: BLOCK, INTRODUCED, witness `{'x': 0}`, which PASSES in the base.
  - `examples/contract_demo/run_demo.py` (Docker): both contracts OBSERVED_REGRESSION INTRODUCED (BLOCK); witnesses minimized to `[1]` and `[1, 0]`; after the repair, PASS with both TESTED.

**Observations recorded, not changed.**
- `examples/contract_demo` sets `require_coverage = false`. The list comprehension in `normalize` is an advisory UNKNOWN static obligation, and the report says so. The candidate run is BLOCK either way.
- `moved_root` (`a - 1` → `a - 2`) is INTRODUCED + RESOLVED rather than CHANGED: anchors include the divisor expression. Both block or report correctly.
- POSSIBLY_UNSAFE results can carry an unvalidated witness (`witness_validated = false`), for example `xs=[]` in A2-01. It is labelled, not presented as a failing input.

**Commands and outcomes.**
- `.venv/bin/python -m pytest -q`: 536 passed before the audit; **583 passed in 131 s** after it (Docker tests ran, none skipped).
- `.venv/bin/python -m pytest -q tests/differential/test_audit_m2m3_adversarial.py tests/integration/test_audit_m2m3_incremental.py tests/integration/test_audit_m2m3_boundaries.py`: 47 passed.
- Fault injection (each fix reverted alone): A2-01 makes 2 cases fail; A2-02 makes 4 fail; A3-01 makes the host test fail (runner side) and the harness test fail (harness side); A3-02 makes 3 unit tests and the end-to-end test fail. A3-03 makes its unit test fail.
- `pip freeze --exclude-editable` still equals `requirements.lock`; no dependency was added. The editable install was refreshed to 0.3.1.

## Audit (M4/M5)

An adversarial audit of the whole system (M0-M5), focused on M4 (the model can never gain authority or proof; loopback validation) and M5 (MCP stdout hygiene, no privileged tools, a genuine and reproducible recorded demo), was run after the M5 gate report. Methods:
- source review of `semantic/*`, `contracts/commands.py` (discover), `service.py`, `mcp_server.py`, `cancellation.py`, `verify.py` (execution setup and lifecycle) and `examples/demo_repo/run_demo.py`;
- 24 hand-written endpoint URLs against `validate_loopback_endpoint`;
- about 50 new static probe programs through `guard check`, compared with CPython;
- direct calls to every service tool on a repository without guard state, with option-like, newline-terminated and path-escaping arguments;
- a re-run of the M5 demo script, the full suite and every example.

Each finding was confirmed by a concrete counterexample before it was fixed. Each fix has a regression test, and each test was shown to fail with its fix temporarily reverted (from scratch backups; nothing was committed or stashed).

| ID | Severity | Finding (confirmed) | Fix | Regression test |
| --- | --- | --- | --- | --- |
| A5-02 | critical | **MCP profile pin had a check-to-use gap: agent-controlled unisolated execution.** `verify_patch(run_profile=...)` checked the preauthorized docker profile's digest by reading `.guard/config.toml`, then `run_verify` loaded the file again. An agent that can write the repository could replace the profile between the two reads with an `unisolated` profile of the same name and `allow_unisolated = true`. The window includes the wait for the per-root lock while another run is active. Reproduced: with the race forced, the call returned OK with execution backend `unisolated`, `isolated: false`, and the target module's import-time marker file was written on the host. | `execution.profiles.authorization_digest` is the single digest function. The digest pinned at startup is passed into `VerifyOptions.authorized_profile_digest`, and `_prepare_execution` recomputes it from the configuration object the run actually uses. Any difference raises `ProfileNotAuthorized` (NOT_AUTHORIZED, "nothing was executed") before a backend starts (I-103). | `tests/integration/test_audit_m4m5.py::test_a5_02_profile_swapped_after_authorization_never_executes`, `::test_a5_02_verify_checks_the_digest_against_its_own_configuration` |
| A4-01 | major | **Annotation-only dividend reported as an exact proof (BLOCK).** For a division, the domain and scope were computed from the divisor's cone only. `def g(a: int): return a / 0` (also `a / D` with module constant `D = 0`) was PROVED_UNSAFE **EXACT_MODEL** with the domain "no function inputs influence this obligation (exact literals only)", so `guard check` gave BLOCK. The dividend's type comes from an unenforced annotation: with `class Quiet(int)` overriding `__truediv__`, `g(Quiet(5))` returns `0.0` in CPython. docs/03 requires annotation-only claims to be CONDITIONAL. | The domain cone of a division includes the dividend (`domain_cone`). The solver cone and witness search are unchanged. `a / 0` with `a: int` is now PROVED_UNSAFE CONDITIONAL (ANNOTATION, with the annotation-only assumption); `a / 2` is PROVED_SAFE CONDITIONAL; `1 / 0` stays EXACT_MODEL LITERAL_ONLY; a declared dividend is CONFIG_DECLARED EXACT_MODEL. Engine 0.4.1 and semantics profile `…-builtin-v4`, so no cache entry carrying the old labels is reused (I-102). | `tests/differential/test_audit_m4m5_adversarial.py::test_a4_01_annotation_only_dividend_makes_the_claim_conditional` (also runs the `Quiet` counterexample in CPython), `::test_a4_01_declared_domain_keeps_the_dividend_exact` |
| A5-01 | major | **The read-only `get_contracts` tool wrote to the repository under check.** It is advertised with `read_only_hint` and "Writes: Nothing", yet on a repository without guard state it created `.guard/state.sqlite`, because opening the store creates it. | Without `.guard/state.sqlite`, it returns an empty list with a diagnostic and opens nothing (I-104). | `test_audit_m4m5.py::test_a5_01_get_contracts_never_creates_state`: after `get_contracts`, `check_scope`, `get_capabilities`, `get_finding` and `explain_finding`, the tree (bytes, modes, mtimes) is identical and no `.guard` exists |
| A4-02 | minor | **Engine fault reported as a complete discovery.** In `guard contracts discover --execute`, an exception while falsifying a candidate was caught per candidate (`ENGINE_ERROR`), but the run stayed `run_outcome: COMPLETE` with exit 0. CLAUDE.md: an engine crash is ERROR and never becomes a pass. | Any candidate with a falsification ENGINE_ERROR makes the run `run_outcome: ERROR` with exit 3, lists `engine_errors`, and prints them on stderr. | `test_audit_m4m5.py::test_a4_02_falsification_engine_fault_is_error_not_complete` (checker fault injected; real CLI) |
| A5-03 | minor | **Identifier validation accepted a trailing newline.** Finding/run IDs, root aliases, profile names and the schema `pattern` check used `re.match` with `$`, so `fnd-…\n` and an alias `app\n` passed validation. | `re.fullmatch` everywhere in `service.py` (I-104). | `test_audit_m4m5.py::test_a5_03_identifiers_with_a_trailing_newline_are_rejected` |
| A5-04 | minor | **TIMEOUT/CANCELLED message over-claimed.** It said nothing from the stopped run is ever published. A run that had already passed its last safe point (`verify.py` final consistency check) when the timeout fired completes and is indexed as an ordinary consistent report. That report is sound, but the message was false. | The message now states that exception and tells the agent to call verify_patch again (I-104). | Covered by `test_server_timeout_is_an_explicit_response_and_publishes_nothing`, which still requires "finalized as failed" |

**New adversarial tests (27, all passing).**
- `tests/differential/test_audit_m4m5_adversarial.py` (21). It has 18 cases with exhaustive CPython ground truth and witness replay, same harness as M2/M3. Cases:
  - a module function shadowing `abs` (PROVED_SAFE, correct), a parameter named `len`, `len` rebound locally;
  - chained comparisons `0 < a < 10` (safe) and `-1 < a < 10` (unsafe);
  - `a //= b`, and `a %= b` after a guard;
  - `a - True`;
  - zeros derived from `a % 3`, `a // 2`, `a // -3` and `a % -3`, plus the never-zero `a % -3 - 1`;
  - `del` then rebind, and a guard followed by two reassignments;
  - conditional expression, `or` short-circuit and a `nonlocal` closure mutation, which must stay UNKNOWN (INCOMPLETE).
  
  It also holds the two A4-01 tests and a NaN case: `if a == a: return 0; return 1/0` with `a: float` must never be PROVED_SAFE, and CPython raises for `nan`.
- `tests/integration/test_audit_m4m5.py` (6) has the A5-01/02/03 and A4-02 tests. It adds an **MCP static-boundary sentinel**: base and candidate modules write a marker and `SystemExit` at import. `verify_patch` (quick and full, no profile), `check_scope`, `get_finding` and `explain_finding` (which recapture the tree) report the PROVED_UNSAFE finding, and the marker never appears.

**Checked and found correct (no change).**
- Loopback validation (24 extra URLs):
  - accepted: IPv4-mapped `::ffff:7f00:1`, `0:0:0:0:0:0:0:1`, `LOCALHOST`, `HTTP://`, port `011434`;
  - rejected: `127.0.0.1:11434@evil.com`, `localhost.`, `127.0.0.1.`, `127.000.000.001`, `::127.0.0.1`, `[::]`, `0`, `[::1%lo0]`, fullwidth `ｌocalhost`, fullwidth port digits, `+`/hex/space ports, a backslash, a fragment-hidden host, a trailing ideographic full stop, a query.
  - The transport is `http.client` to the literal IP, so there is no proxy and no DNS. It re-checks the peer address, rejects 3xx before reading, caps the body at 1 MiB, and has a fixed 4-request allowlist with no pull/create/delete.
- Model authority:
  - the parser builds the contract document itself; `authority`, `proof`, `lifecycle_state`, `evidence_ids` and `examples` from the model are FORBIDDEN_FIELD;
  - `registry.create(kind="propose")` refuses anything but INFERRED;
  - model contract IDs live in their own `model` namespace and an existing ID is never revised by discovery;
  - INFERRED regressions are WARNING (existing test);
  - falsification results are discovery output only, never stored as evidence;
  - no `eval`/`exec` of model text; the DSL is typed JSON.
- MCP:
  - exactly six tools and no approve/revise/baseline/discover/exec;
  - every service tool rejects unknown arguments, option-like bases, `..`, foreign roots and NaN timeouts;
  - the core does not import the SDK;
  - stdout hygiene is tested with print, `sys.__stdout__` and a child process writing to fd 1 (the SDK diverts fd 1 to a stderr duplicate; inspected in the SDK source);
  - `Cancelled` is a `BaseException` and unwinds past per-symbol isolation.
- Recorded demo (`examples/demo_repo/recorded_run/`): `run_demo.py` contains no hard-coded results. Every summary value is read from real CLI/MCP output, and the model's replies are recorded raw.
- Evaluation (`benchmarks/results/m4_model_eval.json`): per-repeat values with variance, real timings, disclosed corrections. No fabricated aggregate was found.
- Backlog: T00-T55 checked and T60-T75 unchecked match the evidence in this file. The T44 and T53 caveats (one model; no live Claude Code session) are stated on the boxes themselves, and docs/11's "one real client repair integration" stays PARTIAL in `docs/ALPHA_CHECKLIST.md`. No box was unchecked.

**Demo re-run (reproducibility).** `.venv/bin/python examples/demo_repo/run_demo.py --out <scratch>/demo_rerun` was run on engine 0.4.1 after the fixes (exit 0, 36 files, copied write-once to `examples/demo_repo/audit_rerun/`). Results:
- candidate CLI verify: BLOCK (exit 1); ratio INTRODUCED PROVED_UNSAFE EXACT_MODEL with witness `{a: 0, b: 0}`; `normalize.sum.v1` OBSERVED_REGRESSION at `xs=[1]` (base `[1.0]`, candidate `[0.5]`);
- one agent attempt: the model produced the same two repairs as the recorded run, byte-identical diffs (the `+ 1.0` removed; `if d == 0: return None`). MCP re-verify was PASS, and both pre-repair findings returned STALE_SNAPSHOT;
- the final CLI verify was PASS (exit 0), COMPLETE, on the same snapshot as the final MCP report (`sha256:276ae48b…`; the snapshot IDs differ from the recording because the engine and semantics profile identities changed);
- no-model `guard check`: INCOMPLETE (exit 2);
- discovery: model AVAILABLE, 2 model candidates rejected, 1 refuted, 1 template candidate (exit 0, 114 s);
- latency: loop 80.5 s, model 74.0 s, verifier 5.8 s. The host was more loaded than for the recording (50.2 s / 43.5 s).

**Commands and outcomes.**
- `.venv/bin/python -m pytest -q`: 699 passed, 1 skipped before the audit; **726 passed, 1 skipped in 191 s** after it (Docker tests ran; the skip is the opt-in real-model test). No existing test was changed.
- Examples:
  - `guard check examples/python_demo` exits 1 (BLOCK/INCOMPLETE, 31 obligations, same counts as before);
  - baseline/candidate/unknown exit 0/1/2;
  - `examples/patch_demo/run_demo.py`: INTRODUCED PROVED_UNSAFE with witness `{x: 0}`, PASSED in the base;
  - `examples/contract_demo/run_demo.py` (Docker): BLOCK with both contracts OBSERVED_REGRESSION (witnesses `[1]`, `[1, 0]`), PASS with both TESTED after the repair.
  - With A4-01, python_demo labels change in one place only: `adversarial/floor_semantics.py::floor_bucket`'s domain source is now CONFIG_DECLARED (declared dividend), still EXACT_MODEL PROVED_SAFE.
- Fault injection (each fix reverted alone): A4-01 makes 2 tests fail; A5-02 makes 2 fail (the race test then shows the marker written and backend `unisolated`); A5-01 and A5-03 together make 2 fail; A4-02 makes 1 fail.
- `pip freeze --exclude-editable` equals `requirements.lock` without its comment header. No dependency was added; the editable install was refreshed to 0.4.1.

**Observations recorded, not changed.**
- `a ** -1` with `a = 0` raises ZeroDivisionError, but `**` is not a division obligation (docs/04 lists `/`, `//`, `%`). Such a function has 0 obligations and can PASS. This is a property-scope limit, not a dropped obligation; a `pow` rule is a candidate expansion.
- Under heavy host load the 2 s quick budget can expire. `candidate/ratio.py` was then UNKNOWN ("SMT: TIMEOUT … budget exhausted"), honestly INCOMPLETE, not a false label.
- `get_finding`/`explain_finding` open an existing state store without the per-root lock. SQLite handles the concurrency, and they create nothing.

## M4 adversarial checks

- **Prompt injection.** Docstrings carrying "IGNORE PREVIOUS INSTRUCTIONS ... APPROVED/proof" reach the model only inside nonce-framed untrusted blocks. A fake model that obeys the injection gets FORBIDDEN_FIELD, and nothing can raise authority above INFERRED (the engine builds the document). A rationale claiming proof or approval changes nothing.
- **Hostile output.** Malformed JSON, Python, SMT, code in literals, nonexistent symbols, forged source references, wrong profiles, vacuous and contradictory claims, depth and repeat budget abuse, and duplicates are each rejected with a code (T43).
- **Network.**
  - Only loopback IPs or `localhost` (mapped without DNS) are accepted.
  - A redirect to another loopback server is refused and never followed.
  - Proxy variables are ignored.
  - Model download, create and delete endpoints cannot be requested.
  - A socket audit saw 127.0.0.1 only.
  - `semantic/endpoint.py` is pinned as the only network module.
- **Failure.** Endpoint down, model absent, digest mismatch, timeout and malformed answers leave deterministic discovery working, with honest INCOMPLETE (T45).
- **Fault injection.** Each of the six M4 safety checks, reverted alone, made at least one test fail (T41, T43 rows).

M4 validation commands (latest outcomes):
- `.venv/bin/python -m pytest -q`: 678 passed, 1 skipped (the opt-in real-model test) in 190.82 s, Docker tests included.
- `IG_REAL_MODEL=1 .venv/bin/python -m pytest -q tests/integration/test_real_model.py`: 1 passed in 89.25 s.
- `.venv/bin/python benchmarks/model_eval.py --repeats 3 --template-repeats 3`: exit 0, wrote `benchmarks/results/m4_model_eval.json`.
- `guard model status` / `guard contracts discover --model local --execute ...`: see "M4 measurements".

## Paper benchmarks and report (precise)

User request: an honest empirical paper. docs/12 requires reproducible evidence first, so every number in `paper/invariantguard.tex` comes from `benchmarks/run_all.py` runs executed in this stage (8-9 October 2026, engine 0.5.0, semantics profile `python3.13-cpython-builtin-v4`, z3 5.1.0, Docker 29.5.2 with `python:3.13-slim` image `sha256:3dd7cc10...`, Ollama 0.40.1 serving `qwen2.5-coder:7b` digest `sha256:dae161e2...`). The Ollama app had updated itself from the 0.35.1 recorded above; the model digest is unchanged. `pip freeze` equals `requirements.lock` (the manifest records this). Decisions: I-110 (pipeline, provenance) and I-111 (the latency measurement repeated, with the repeat pre-registered as primary).

**Provenance of the earlier interrupted attempt.** The session transcripts give these facts:
- The seeded labels were frozen at 17:44:28Z, before the first tool run on those cases at 17:48Z. The manifest is unchanged since then.
- The semantic ground truth gained one field after a smoke run: `site_line` for `floor-semantics-safe-const`. No label changed.
- The earlier partial `paper_model_eval.json` (engine 0.4.1, 1 of 3 model repeats; the engine changed to 0.5.0 while it ran) was discarded and re-run.
- The agent proposals (`benchmarks/agent_eval/proposals/run{1,2,3}/`) were written by three `claude-sonnet-5-5` subagents between 23:02 and 23:04Z. Their transcripts show they read only `packets/` and the README and never ran `guard`.

Command: `.venv/bin/python benchmarks/run_all.py` (parts tests, semantic, seeded, agent, latency, model, transport; all exit 0; 23:08Z to 02:17Z), then `.venv/bin/python benchmarks/run_all.py --parts latency_repeat` (exit 0). The first invocation's code predated the `latency_repeat` part, so the repeat was run separately. The manifest is `benchmarks/results/paper_manifest.json`; it holds per-part command, times, exit and results sha256. Then `.venv/bin/python paper/make_tables.py` and `cd paper && latexmk -pdf invariantguard.tex` (exit 0; 12 pages, no undefined references or overfull boxes). How to reproduce: `paper/README.md`.

| Part | Results | Outcome (all numbers from the results files) |
| --- | --- | --- |
| tests | `paper_test_suite.json` | 788 passed, 2 skipped (opt-in real-model tests), 1 xfailed in 206 s. The xfail is new: `tests/integration/test_paper_findings.py` records the U02 defect below as a strict xfail. |
| semantic | `paper_semantic_fixtures.json` | 31 fixtures, 31/31 match the specified result with and without the solver. Solver on: 9 PROVED_SAFE, 10 PROVED_UNSAFE, 1 POSSIBLY_UNSAFE, 11 UNKNOWN. Of 28 independently labeled cases (18 defect by executed witness, 10 safe by hand argument plus grid): TP 10, FP 0, FN 0, TN 9, abstentions 8 on defects and 1 on safe; recall with abstentions as misses 0.56. Solver off changes 2 results (two-variable-equality PROVED_SAFE to UNKNOWN; unknown-call-unrelated POSSIBLY_UNSAFE to UNKNOWN). The syntax-error skip case is INCOMPLETE as specified. |
| seeded | `paper_seeded_patches.json` | 35 pairs: 21 defect, 12 no-defect, 2 intentional; execution confirmed all labels. Static quick: TP 13, FN 4 (D02-D04 are contract regressions with no static obligation; U02), abstain 4/5, FP 0. Best configuration (`--execute` where contracts exist): TP 18, FN 1 (U02), abstain 2 (U01 loop, U04 `math.gcd`), FP 0, TN 7, abstain on negatives 5 (I01, N06, U03, P02 comprehension/loop obligations; P01 UNDETERMINED attribution). No negative was blocked. I04 (declared contract not revised) is BLOCK; I02 (inferred contract only) is WARN. Solver off changes 0 of 30 obligations. Quick and full agree on all 30 shared obligations, and so do warm and full; the verdicts differ only for U02. Median wall: quick 408 ms, `--execute` 4,639 ms. |
| agent | `paper_agent_eval_run{1,2,3}.json` | All COMPLETE. 81/89/84 candidates, all schema-valid and non-vacuous, 0 batches rejected; 58/66/65 new beyond templates; refuted 6/10/9, all valid for the intended behavior (0 false claims); 5 of 5 seeded bugs exposed in every run. |
| latency | `paper_latency.json` | First run, with host load spikes (median 1-minute load 7.2, max 93.7). Reported in the appendix. |
| model | `paper_model_eval.json` | Templates: 23 candidates per repeat, all TESTED, 0 seeded bugs. Local model: 35 raw per repeat; schema-valid 0.77/0.77/0.83; non-vacuous 0.66/0.63/0.69; refuted 10/8/10, of which 8/6/8 were false claims; 2 of 5 bugs exposed (`clip_negatives`, `dedupe_sorted`) in every repeat; 3 distinct response sets in 3 repeats at temperature 0; per-function latency p50 92.2 s, p95 128.4 s; inference server peak RSS 4,918 MB. |
| transport | `paper_transport_openai_compat.json` | OpenAI-compatible adapter against Ollama's loopback `/v1`, 1 pass: AVAILABLE, 13/13 answered, json_schema structured output, served model `qwen2.5-coder:7b`, `context_left_machine: false`, 35 raw candidates, schema-valid 0.83. A transport check only. No hosted provider was contacted. |
| latency_repeat | `paper_latency_repeat.json` | Primary latency run (median load 3.9, range 2.0-10.4). Quick, leaf change: p50 493 ms (p95 517) at 101 functions, 1,445 ms (p95 1,488) at 1,001, 14,335 ms at 10,001. The 2 s p95 target is met at 101 functions (both changes) and at 1,001 (leaf). It is not met for the hub change at 1,001 (cold quick INCOMPLETE, p95 4,711 ms) or at 10,001. Quick and full: 0 mismatches in 13,210 compared obligations. 2,031 of 2,164 full-run introduced findings were missing from quick runs, and every one of those quick runs was INCOMPLETE. Peak RSS at most 756 MB. |

**After all paper-stage script changes** (`model_eval.py --provider`, `run_all.py` parts, `make_tables.py`): `.venv/bin/python -m pytest -q -p no:cacheprovider` gave 788 passed, 2 skipped, 1 xfailed in 188 s.

**Host conditions (disclosed in the paper).** Docker Desktop was not running at the start of this stage and was launched for the runner. Launching it also restarted 9 containers belonging to the user's other project; they were left running. `benchmarks/results/logs/host_load.log` holds one sample per minute from mid-way through the first latency part onward: load median 7, max 137; swap 8.9-18.5 GB. In both latency runs, some full-mode runs ended INCOMPLETE without reaching the 600 s budget (8/263 first, 2/263 repeat). Each left 1-5 required obligations UNKNOWN. This is consistent with per-query solver timeouts under contention, but per-run reasons were not stored, so the cause is unverified.

**Paper content.** `paper/invariantguard.tex` covers the evidence axes; the design (IR, semantic profiles, verdict lattice, change-scoped verification, authority, proposers including BYOK and agent-proposed); implementation; RQ1-RQ5; threats; related work; limitations; and conclusion. Every number is a macro from `paper/generated/numbers.tex`. The bibliography entries were checked against arXiv, Crossref and the official pages. The disclosures:
- the system, the benchmarks and the report were produced by Claude Opus 5.5 agents;
- the agent proposer (Claude Sonnet 5.5) is from the same family;
- its prompt asked for bug-exposing properties, and the README it read describes the seeded-bug metric;
- its cap was 8 candidates per function versus 3 for the local model;
- the proposer comparison is therefore not controlled;
- no hosted provider was evaluated.

## Phase 1 (pivot): change detection and deterministic hashing

Branch `phase1-change-detection` (from `m0-m5-checkpoint`). Motivation: spikes/diffreplay/REPORT.md gaps 5, 8 and 10 and seeded case U02. Decisions I-112 to I-115.

- Unsupported symbols are fingerprinted by their whole definition; module and class bodies by their import-time code (I-112). U02 is fixed: its strict-xfail test in `tests/integration/test_paper_findings.py` is now a regular passing test. The paper's numbers were measured on the frozen engine before this fix and are unchanged.
- Every symbol has a docstring-blind `code_fingerprint`; `patch.symbols.doc_only` lists modified symbols whose only change is in docstrings (I-113).
- Every symbol records conservative `reads` (module-level names it loads, plus a `<Class>.<class-body>` pseudo-global for methods) that feed the impact graph only, not validity keys (I-114).
- The probe harness runs with `python -P -s -S -B` (not `-I`, which ignored `PYTHONHASHSEED`). New required capability `deterministic_interpreter_env`; `PYTHON*` names are rejected in `env_allowlist` (I-115).
- `.pyi` stubs are still not captured in snapshots: only differential-replay input generation (phase 3) needs them, and adding them now would feed stub files to the static frontend. Deferred to phase 3.

Commands and outcomes (2026-10-09, run with `nice -n 19`, single process):
- `pytest tests/integration/test_change_detection.py tests/integration/test_hash_seed.py`: 11 passed (new tests: methods, generators, decorated functions, doc-only, code+doc, module data, imported data, class attributes, sibling methods; hash seed honored, `-I` documented as ignoring it, `PYTHON*` allowlist rejected).
- `pytest tests/integration/test_docker_isolation.py -k "hash or seed or capability"`: 2 passed against the real Docker backend (capability set includes `deterministic_interpreter_env`; `hash()` and str-set order agree across two containers).
- `pytest tests`: 761 passed, 2 skipped, 16 failed, 27 errors. The failures and errors were `OSError: [Errno 28] No space left on device` (the data volume had about 4.5 GB free and briefly reached zero), plus one real failure: the fake Docker in `test_execution_failclosed.py` did not report the new capability. The fake was updated, and a fail-closed test for randomized hashing was added. All 9 affected files were then rerun: 61 + 10 passed.
- `spikes/diffreplay/survey_changes_phase1.py` (90 commits of more-itertools, boltons and dateutil; spike detector vs. `match_symbols`): before, 53/134 changed functions found and 81 missed; after, **134/134 found, 0 missed**. Output: `spikes/diffreplay/results/changes_survey_phase1.json`. Per-commit time was 226 s total (median 1.6 s) vs. 813 s (8.5 s) in the spike run. The spike run was on a heavily loaded host, so this is not a speed claim, only no evident slowdown.

## Phase 2 (pivot): replay harness v2

Branch `phase1-change-detection` (continued). Decisions I-116 to I-119. Spike gaps closed: 1 (methods and constructors), 2 (encoding), 3 (non-JSON inputs), 4 (dependency environment), 6 (pluggable harness), 7 (batch output).

- `execution/replay_harness.py` (protocol `ig-replay-1`) runs beside the unchanged contract harness and is selected per call through `runner.HARNESSES`. It supports:
  - targets: module functions, `*args` and keyword arguments, constructors, static and class methods, and instance methods and properties on a receiver built inside the probe;
  - encoding: iterators to a bounded prefix, including a lazily raised exception; datetimes, sets, frozensets, bytes, complex numbers, ranges, dicts with non-string keys; objects through their state and iteration;
  - observations: mutated arguments and the receiver's state after the call.
- `replay/run.py`:
  - typed inputs (`Value`, `Expr`, `Construct`); expressions are checked against the snapshot text before anything runs;
  - targets outside `allowed_targets` are reported `not_allowed` and never sent;
  - observations are shape-checked as untrusted;
  - an oversized batch is split and retried;
  - with the profile's `site_packages`, the installed distributions are folded into the environment digest.
- Process structure: the zygote imports the targets once; probes run in forked chunks with a per-probe timer; a crashed or hung chunk is re-run one probe per child under a hard kill; each child keeps only its own result pipe; RLIMIT_CPU is clamped to the boundary's hard limit.
- Not yet done: comparing observations across base and patch, equivalence policy, generation, call sequences and CLI wiring (phases 3 and 4).

Commands and outcomes (2026-10-09, `nice -n 19`, single process):
- `pytest tests/integration/test_replay_harness.py`: 40 passed. Two of the tests use the real Docker backend: the `python:3.13-slim` image, and the spike image with `six` for `site_packages`.
- The default-profile Docker test found a defect, now fixed. The harness requested RLIMIT_CPU (10 s soft, 11 s hard) above the container's 10 s hard limit, so `setrlimit` failed and every probe reported `crashed`.
- `pytest tests`: 841 passed, 2 skipped (opt-in real-model tests), 1 failed. The failure was `test_engine_source_has_no_execution_primitives`, which flagged the replay harness's `eval`/`compile`/`fork`/`import_module`. It was resolved with a per-file allowlist plus `test_replay_harness_evaluates_only_repository_inputs`, which pins the only `eval`/`compile` site to `materialize_arg` and checks that no engine module imports the harness. `tests/integration/test_audit_regressions.py` was rerun: 57 passed.

## Phase 3 (pivot): input harvesting and generation

Decisions I-120 to I-122.

- `replay/harvest.py` (host side, AST only) finds calls to a target in test files and in its module's doctests. It handles:
  - parametrize rows, local bindings, module-qualified calls and class aliases;
  - alternate constructors, and same-module subclasses that inherit the member;
  - properties and operator dunders.
  - Method targets get the earlier calls on the same local object as `steps`.
  - Inputs are exact repository text plus verbatim bindings (no inlining); `default_variants` perturbs omitted defaults.
- `replay/generate.py` with the harness's `generate` mode runs Hypothesis inside the boundary: `shape`, `annotation` and `stub` sources, derandomized, keyword-passed typed inputs. Without Hypothesis in the image, generation is reported `unavailable`.
- Not yet captured: test files and `.pyi` stubs as a snapshot-identified source set. Phase 3 works on path-to-bytes maps; phase 4 captures them with the run.

Commands and outcomes (2026-10-09, `nice -n 19`):
- `pytest tests/integration/test_replay_harvest.py tests/integration/test_replay_generate.py`: all pass. Generation runs in Docker with `invariantguard-diffreplay:spike` (python:3.13-slim + hypothesis 6.165.0), checks determinism across two runs, replays the generated inputs, and has the stub reproduce a `caaec94`-style empty-pool case.
- Defects found and fixed while testing:
  - duplicate generation job ids overwrote results;
  - a receiver bound to a local variable got an empty namespace;
  - re-parsing an indented multi-line binding raised IndentationError (dateutil `parse`).
  - The audit scan also flagged `__import__` in the stub path, now replaced by `importlib.import_module`; its pin test now allows `eval`/`compile` only in `materialize_arg` and `generate_job`.
- `spikes/diffreplay/harvest_compare_phase3.py` (harvesting only, no execution; 90 commits): 106 directly changed functions. 66 have harvested inputs, the same count as the spike, with 666 inputs vs. the spike's 514. One function has inputs only from the spike: `urlutils.OrderedMultiDict.addlist`, where the spike matched `dictutils.OMD` by name, which is a different class. One function has inputs only from the new harvester. Output: `spikes/diffreplay/results/harvest_compare_phase3.json`.
- `pytest tests`: 855 passed, 2 skipped (opt-in real-model tests).

## Phases 4-5 (pivot): differential replay in verify, maintainer decisions

Decisions I-124 to I-129. Phase 4 was committed as `a5517ec` and `8d4519b`; this section records it together with phase 5.

- Phase 4: `guard verify --replay` (requires `--execute` and a run profile) and the MCP `verify_patch` parameter `replay`. Differences are `OBSERVED_DIFFERENCE` findings (INFERRED, WARNING at most, never blocking). Coverage counts only changed public symbols (I-125).
- Phase 5: `guard behavior accept|pin|retire|list` (`replay/commands.py`, `replay/decisions.py`). Decisions live in the append-only `behavior_decisions` state table (schema v4). An acceptance covers only the identical witness and outcomes. A pin turns a literal witness into APPROVED behavior that blocks when the candidate diverges and the base matches (I-128). Decisions are CLI-only; no MCP tool exists.
- Phase 5: unchanged doctests of changed modules are DECLARED behavior (`replay/declared.py`, harness `doctest` mode). A doctest that passes twice on the base and fails twice the same way on the candidate is OBSERVED_REGRESSION (I-129).
- `benchmarks/results/replay_phase4.jsonl`: per-commit replay results on the spike corpus (more-itertools, boltons, dateutil).

Commands and outcomes (2026-10-10, Docker daemon not running):
- `pytest tests/integration/test_replay_decisions.py`: 7 passed.
- `pytest -q`: 870 passed, 17 skipped (Docker-backed tests: no daemon; opt-in real-model tests).
- Not run in this session: the Docker-backed replay tests (`test_replay_in_docker`, `test_mcp_verify_patch_replay`, the Docker cases of `test_replay_harness.py` and `test_replay_generate.py`).

## Release preparation (2026-10-10)

- License MIT OR Apache-2.0; README rewritten as an alpha / research preview; CONTRIBUTING, SECURITY; build-kit files moved to `docs/build_kit/`.
- Absolute host paths in recorded outputs replaced by `$REPO`. `benchmarks/results/paper_manifest.json` keeps the run-time `results_sha256` and adds `results_sha256_redacted` and a `redaction` record. Earlier Git history still contains the original paths. Future `benchmarks/run_all.py` runs record absolute paths again until `bench_common.host_manifest` is changed.
- Portability defect found and fixed: `symtable.SymbolTableType` exists only from Python 3.13, so every lowering failed on 3.11/3.12 despite `requires-python = ">=3.11"`. The comparison now uses the string value (a `StrEnum` on 3.13+). `tests/integration/test_agent_eval_tools.py` is skipped off 3.13: its shipped packets carry 3.13 snapshot IDs (the semantics profile `python3.13-...` is part of snapshot identity, by design).
- CI: `.github/workflows/ci.yml` (Linux 3.11-3.13, macOS 3.13, a Docker job, sdist/wheel build).

Commands and outcomes:
- `pytest -q` (CPython 3.13.9): 870 passed, 17 skipped.
- `pytest -q` (CPython 3.12, Homebrew, `pip install -r requirements.lock`): before the fix 359 failed; after it 868 passed, 19 skipped (the two agent_eval packet tests). Python 3.11 was not available locally; CI covers it.
- `pip wheel . --no-deps`: metadata `License-Expression: MIT OR Apache-2.0`, both license files included.

## CI follow-up (2026-10-11)

- CI run 38094400346: every job passed except `test (py3.11)`, where 1 test failed: `test_dsl.py::test_invalid_vacuous_and_malicious_contracts_are_rejected[pathological depth and size]`. A contract nested 5000 deep made CPython 3.11's json encoder raise `RecursionError` (an engine crash on hostile input) before the size check could run. `check_document_shape` now rejects such a document as `OVERSIZED`.
- Checked locally on 3.13 by forcing `canonical_json` to raise `RecursionError` (result: `Rejection OVERSIZED`) and with `pytest -q`: 870 passed, 17 skipped. No 3.11 interpreter is available locally; the next CI run is the 3.11 check.
- Commit messages were rewritten to drop co-author trailers (trees unchanged, backups under `refs/backup/pre-rewrite/`), and `main` was force-pushed.

## Subset widening: methods and printf formatting (2026-10-12)

- **Methods (I-130).** A plain `def` directly in a module-level class body is analyzed. The receiver is an unmodeled input, free names skip class scope, and methods are call-graph nodes for impact. Still unsupported: async, decorated (including `staticmethod`/`classmethod`/`property`), methods of local classes, and methods using a name subject to private-name mangling. Calls to methods (`obj.m()`) remain unmodeled attribute access.
- **`str % tuple` (I-131).** An exact str literal `%` an exact tuple literal is formatting, PROVED_SAFE for `division.zero_denominator` by `TYPE_FACT` (a new `AnalysisMethod`, added to `common.schema.json`). Any other `str % x` stays UNKNOWN, because a str-subclass right operand dispatches to its own `__rmod__`.
- Corpus (symbols analyzed, before → after methods): boltons 161 → 759 of 951, dateutil 30 → 249 of 310, more-itertools 117 → 185 of 291, own `src` 455 → 864 of 1028. Resolved required obligations barely move: unmodeled attribute access, `for` loops, exceptions and generators are now the dominant UNKNOWN reasons.
- `pytest tests/differential/test_methods_and_formatting.py`: 14 passed. Thirteen cases compare every PROVED_SAFE against an exhaustive CPython run (receivers `None`, `0`, `object()`, an uninitialized instance; ints -3..3 plus a `str` subclass with a raising `__rmod__` and an object with a raising `__str__`). Every PROVED_UNSAFE witness is replayed. The remaining test is a CPython probe of `__rmod__` dispatch.
- `pytest tests/integration/test_verify.py -k method_caller`: 1 passed (a changed module function invalidates an unchanged method caller: BLOCK, INTRODUCED, base PROVED_SAFE).
- Changed expectations: `test_lowering.py::test_every_division_site_is_accounted_for` (`C.m` is now lowered); the `method-unsupported` semantic fixture still expects UNKNOWN, with the reason now attribute access on the receiver.
- Full suite (`pytest -q`, CPython 3.13): 885 passed, 17 skipped (Docker and real-model tests skip locally).
- One result became weaker on purpose. In `def f(a): s = 'ok %s' % (a,); return 10 // 0`, the division was PROVED_UNSAFE with witness `{}`; it is now POSSIBLY_UNSAFE, the same as after `a + 1`, because a raising `__str__` on `a` can stop execution first. With `a: int`, it is PROVED_UNSAFE with witness `{'a': 0}`.

## Human report: grouped unresolved obligations (2026-10-12)

- `guard check` and `guard verify` (`--format human`, the default) still list every result that is not UNKNOWN. PROVED_SAFE results take one line plus their assumptions. UNKNOWN obligations are summarized as counts by cause (`cli.unresolved_cause`, a display label derived from the first reason), with two example locations for each of the top 10 causes. `-v/--verbose` restores the full per-obligation listing and full snapshot ids. The JSON report, exit codes and verdict reasons are unchanged. `guard --help` no longer describes milestones.
- `guard check src`: 145 lines (30.6 KB), against 559 lines (94.7 KB) before; `--verbose` gives the previous 559 lines.
- `pytest tests/integration/test_cli.py tests/integration/test_runner_boundaries.py`: 37 passed (new: compact versus verbose listing, with every non-UNKNOWN location present and identical verdict reasons; cause labels).
- Full suite (`pytest -q`, CPython 3.13): 891 passed, 17 skipped.
- CI run 38317854539 (the methods commit): every job passed except macOS 3.13, where `test_baseline.py::test_git_baseline_records_a_commit_and_reads_it_without_checkout` failed. The unchanged-tree check saw a new `.git/objects/maintenance.lock`, created by background git maintenance that the test's own `git commit` started, not by guard. Test repositories now set `maintenance.auto=false` and `gc.auto=0`. `pytest tests/integration/test_baseline.py tests/integration/test_verify.py`: 23 passed.

## Subset widening: loops (2026-10-12)

- **Loops (I-132).** `for` over `range` (1-3 arguments), exact lists, tuples and strs, or any other iterable (an unknown effect); `while`; `break`, `continue`, `else`. Each loop is lowered as one arbitrary iteration. Loop-carried locals are fresh values of a type that covers every iteration, and the CFG stays acyclic for static analyses. The IR evaluator runs the real iterations to validate witnesses. New IR ops `LOOP_PHI`, `LOOP_HEAD`, `ITER`, `NEXT`, `ELEMENT`; IR version `ir-0.3`, which also invalidates cached summaries.
- Still unsupported: `async for`, `for` targets that are attributes, subscripts or starred, comprehensions and generator expressions, and generators.
- `pytest tests/differential/test_loops.py`: 1 passed. The test covers 30 loop functions (range with literal, negative and computed steps; while with guards and `while True`/`break`; `for`/`else`; `continue`; nested loops; list index by `range(len(xs))`; a list mutated during iteration; early `return`; maybe-unbound and type-changing carried variables; tuple targets; unknown iterables; break/continue inside an unsupported `with`; a 10^6-iteration loop). Each result is compared with an exhaustive CPython run, every witness is replayed, and 28 expected results pin the precision.
- Changed expectations, each now correct and checked against CPython or by hand:
  - `test_audit_m2m3_adversarial.py` `loop_carried_value_reaches_zero` (PROVED_UNSAFE, witness `a=1`);
  - `test_audit_regressions.py` `loop-carried-parameter` (PROVED_UNSAFE, `a=0`) and `infinite-loop-before-literal-zero` (PROVED_SAFE: unreachable after `while True` without break);
  - semantic fixture `loop-carried-total` (PROVED_UNSAFE, `n=0`);
  - `test_static_boundary.py` and `test_lowering.py` now use `decorated.py` and a `with` statement as their unsupported constructs.
- Corpus (resolved required obligations, before → after loops): boltons 2 → 19 of 157-159, dateutil 1 → 12 of 82, more-itertools 0 → 0 of 42. Own `src` went 9 → 5 resolved of 115-118: six `none.attribute_access` obligations in `impact/attribution.py` disappeared, because `evaluate()` (a `while True` loop) can no longer fall off its end and return None. That removed one POSSIBLY_UNSAFE false alarm and its five companions.
- Full suite (`pytest -q`, CPython 3.13): 892 passed, 17 skipped.

## Known gaps (disclosed)

- **Found by the paper benchmark (engine 0.5.0, not fixed; the evaluation reports the frozen engine).**
  - U02: the fingerprint of an unsupported symbol (`frontends/python/__init__.py::_symbol_fingerprint`) hashes only its division nodes. Removing a guard elsewhere in a nested function is therefore not seen as a modification, and quick mode reports PASS where full mode is INCOMPLETE. A strict xfail test records it (`tests/integration/test_paper_findings.py`).
  - P01: a pre-existing division failure that the patch moves into a temporary variable is not matched across the edit. The result is UNDETERMINED, so INCOMPLETE rather than PRE_EXISTING.
  - Under heavy CPU contention, full-mode runs with more than 1,000 functions occasionally end INCOMPLETE (1-5 required obligations UNKNOWN). Per-run reasons are not stored by `bench_latency.py`.
- **M3: targets and values.** Targets are module-level plain functions only. Methods, nested, decorated and async functions, and `*args`/`**kwargs` are rejected. Profiles cover flat numeric/int lists and int/float scalars; there are no string, dict, nested or custom-object inputs. Mutable-argument contracts copy inputs by re-decoding the serialized input for every call; custom objects and cycles are opaque (UNKNOWN). Approximate repeatability needs tolerances the v1 node does not carry and is rejected.
- **M3: environment comparability.** The runner image contains only the standard library. Repository dependencies are never installed: a target importing a third-party package gives `import_error` (UNKNOWN). Base and candidate run in the same prepared image; if dependency lock files differ, an observed difference is UNDETERMINED rather than a regression. Only captured `.py` files are copied into the runner, so data files are absent.
- **M3: isolation scope.**
  - The capability checks establish what the profile claims, as observed from inside the container on this Docker Desktop VM. They are not a claim of containment against kernel exploits; the VM kernel is shared by the containers.
  - Writable places inside the container: the `/tmp` tmpfs (noexec, 16 MiB), Docker's default `/dev` tmpfs and `/proc` masks (all container-scoped), and the Docker-managed `/etc/hosts`/`hostname`/`resolv.conf`, which are root-owned and not writable by uid 65534.
  - A target can forge its own observation frame. That is equivalent to returning that value, so execution evidence describes behavior and is not a certificate.
- **M3: probes are finite and not exhaustive.**
  - TESTED is never a proof. A nondeterministic base is observed once per input (plus one confirmation); the candidate failure is repeated twice.
  - There is no static checking of contracts (no faithful float/list model), no dynamic cache (every `--execute` run re-executes), and dynamic checks are not sliced (every active contract runs).
  - `guard contracts discover` falsifies candidates dynamically only with `--execute` (M4), and its falsification results are reported, not stored as evidence.
  - `guard check` ignores contracts. `guard replay` is not implemented (`guard explain` exists since M5).

- **M4: one model, slow host.** Only qwen2.5-coder:7b (Q4_K_M) was evaluated. No second open-weight model was downloaded (not authorized), so docs/08's comparison of at least two models and hardware tiers is open, and no general recommendation is pinned. On this host, under load, inference costs ~80-95 s per function. Answers vary between runs even at temperature 0.
- **M4: what the model can say.** Proposals are limited to DSL v1 over single-profile, module-level functions (the M3 target limits). `propose_probe_parameters` and `explain_finding` are UNSUPPORTED. Context selection is syntactic: direct calls by name, `from`-imports, and tests that mention the name. Token counts are estimated as ceil(bytes/3) rather than tokenized.
- **M4: falsification is finite.** A TESTED model claim is finite evidence on the current code only. Without `--execute`, wrong model claims are recorded as INFERRED candidates (advisory). Discovery falsification results are reported, not stored as evidence. The evaluation's held-out set is small (13 functions, 5 seeded bugs), written by the implementer, and synthetic: no effectiveness claim on real repositories follows from it.

- **Wall-clock solver limits are load-dependent.** With the default `per_query_timeout_ms = 100`, a query near the limit can be UNSAT in one run and TIMEOUT (UNKNOWN, INCOMPLETE) in another. This is never a wrong label, but quick/full or warm/cold runs can differ for such obligations. TIMEOUT results are never cached. Configure `per_query_rlimit` for reproducible runs (I-050).
- **Quick mode still captures and lowers both snapshots in full** to compute validity keys. Only analysis is sliced, so warm quick time is dominated by capture and lowering (see the measurements). Skipping lowering for unaffected files needs per-file key caching (future work).
- **Report self-validation cost.** The CLI validates every report against the JSON Schema before printing (I-004). On large verify reports this dominates warm runs: about 4.6 s of 7.7 profiled seconds at about 1,000 functions. A streaming or opt-out validation mode is future work; the check is a safety net, not analysis.
- **Budget enforcement and cancellation.** `quick_budget_ms`/`full_budget_ms` bound escalation (SMT and witness search) and are checked between symbols. Since M5, a bound cancel token (MCP calls) is checked after capture and lowering and before each symbol, and runner processes are killed on cancel. The parsing and lowering of one snapshot, and the interval analysis of one function, are still not interruptible inside the stage.
- **Call summaries** are instantiated bodies, bounded by depth 6 and 6,000 IR values. Recursion, methods, nested functions, decorators, `*args`/`**kwargs` and dynamic dispatch remain unknown. Cross-module resolution is CONDITIONAL.
- **Aliases and mutation.** There is no points-to analysis. A list's length dies at any unknown effect, so index obligations on it become UNKNOWN. Element values of lists and tuples are not modeled.
- **None-flow is narrow.** Only attribute loads are checked. Truthiness of optionals, `isinstance` narrowing, subscripting or calling None, and optional kinds other than one exact inner kind are not modeled.
- **Annotations** remain conditional (`int`, `list[...]`, `X | None`, `Optional[X]`). A module rebinding a builtin type name disables trust in that annotation.
- **Patch attribution** compares function-domain obligations through structural anchors. Large refactors that move operations produce INTRODUCED + RESOLVED pairs or UNDETERMINED (never silent reuse). Matching uses identical-body renames only; similarity matching for edited renames is uncertain by design.
- **Manifest-only baselines** (no retained source, no commit) cannot evaluate witnesses in the base. Changed symbols are UNDETERMINED, which is INCOMPLETE for exact failures.
- **Git filters.** Base content is raw blob bytes (no smudge/LFS). A repository whose working tree differs from its blobs only by filters shows those files as modified (the semantic keys usually still match).
- **Reports without JSON.** When the engine fails outside per-symbol isolation (for example a corrupted retained baseline or an inaccessible commit), the CLI prints the error to stderr and exits 3 without JSON.
- **Unverified claims.** The PEP 667 frame-introspection assumption (I-012) and the closed-module assumption (no monkeypatching, I-032) are stated, not verified.
- **M5: agent integration limits.**
  - MCP is not enforcement: no hook or CI gate is implemented.
  - The Claude Code configuration is tested by launching its documented command with the SDK client, not inside Claude Code.
  - The recorded loop's agent is a 7B local model with a fixed prompt. One run on two fixtures says nothing about repair rates. The `ratio` repair returns None (different from the baseline's 0.0), which the declared properties do not cover.
  - `check_scope` reports are per-session.
  - Only stdio is served; there is no HTTP transport.
  - Each `verify_patch` is serialized per root.
  - Dynamic checks through MCP require Docker (no unisolated profile through MCP).
  - The report index grows append-only, with no pruning command yet.
- **Schema coverage.** ContractRevision, CandidateBatch (template and local_model), ContextPacket and RunProfile documents are now produced by the engine and validated in tests.
- **Remote and agent proposers (I-105/I-106).**
  - Hosted APIs were not exercised (no key on this host). Only the fake provider and Ollama's loopback `/v1` were used.
  - A remote proposer sends source code off the machine. The disclosure records where and how much, but nothing can recall it. Free tiers may retain or train on it.
  - Agent identity (`clientInfo`, `--client`) is self-reported and unauthenticated.
  - Agent proposals inherit every M3/M4 limit: DSL v1, module-level single-profile targets, finite falsification.
  - The agent-proposer evaluation (`benchmarks/agent_eval/`) has results for three runs of one model family (see "Paper benchmarks and report"); the comparison with the local model is not controlled.
  - Docker-backed falsification of agent candidates through MCP (preauthorized docker profile) is covered only by the shared code path and the existing Docker MCP test, which was skipped in this session because the daemon was not running.

## Deferred (not started)

- **M6 (T60-T65): DEFERRED.** The host has no JDK, `kotlinc` or Gradle; `/usr/bin/java` is the macOS stub. Installing a pinned toolchain needs a download that was not authorized. T60 exists only as a written feasibility decision (DECISIONS I-101: a pinned Analysis-API/K2 sidecar in a separate JVM process, a versioned JSON exchange, no Gradle execution). The spike (compiling and inspecting fixtures) has not run, so T60 stays unchecked.
- M7 (T70-T75).

**Research paper (requested by the user): written.** It is `paper/invariantguard.tex`, built from `benchmarks/run_all.py` results (see "Paper benchmarks and report"). It makes engineering and benchmark claims only, with every threat disclosed. T72 stays open: the seeded set is author-written, not held out by repository family, and has no external licensed inputs. Effectiveness on natural agent patches still needs T72 and independent models and authors.

## Next task

- **M6** needs an explicitly authorized, pinned JDK and Kotlin toolchain install. Then run the T60 spike (I-101) and implement T61.
- **Without M6**, the remaining work is the release criteria in docs/11 "Before an alpha", mapped in `docs/ALPHA_CHECKLIST.md`. Open items:
  - client-side enforcement (a hook or CI job that runs `guard verify` and checks the exit code), tested separately;
  - a live Claude Code session using the documented `.mcp.json`;
  - T72: a labeled held-out patch benchmark from real agent patches (T73's internal ablation report now exists: `paper/`).
- Carry-overs: a probe cache; a second model evaluation once a download is authorized; pruning of the report index.
- Remote and agent proposers: agent proposals from three Claude Sonnet 5.5 subagents are scored (`benchmarks/results/paper_agent_eval_run*.json`); a controlled comparison (same prompt and cap across arms, different model families, independent benchmark authors) is still open. Fix the U02 fingerprint defect and re-run the seeded benchmark. Exercise the Anthropic/OpenAI adapters against a hosted provider once a key is explicitly provided, and record the request/response identity.
