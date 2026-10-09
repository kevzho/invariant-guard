# InvariantGuard build kit

Version 1.0 | 4 October 2026 | Implementation specification, not an implemented product

InvariantGuard is a local-first, change-scoped verification layer for coding agents. It learns repository-specific behavioral contract candidates, checks relevant contracts and modeled runtime-failure paths, and returns evidence into an agent repair loop. Open-weight inference proposes semantic properties; mechanical analysis and explicitly enabled execution establish the evidence.

## Start here

1. Put this entire folder at the root of the repository you want Claude to build.
2. Keep `CLAUDE.md` at that root. Keep these specifications in `docs/`.
3. Start Claude in the folder and paste the opening task from `BUILD_START.md`.
4. Build milestone M0, then M1. Use `docs/15_TASK_BACKLOG.md` as the checklist.
5. Read the PDF manifest for the same complete plan in a reviewable document.

This kit started as planning documents only. Milestones M0-M5 are now implemented:

- a static Python slice (`guard check`, `guard capabilities`, `guard init`);
- change-aware static verification (`guard verify --base REF|baseline:LABEL --mode quick|full`, `guard baseline create|list|show`);
- behavioral contracts:
  - a typed DSL;
  - lifecycle and authority (`guard contracts list|show|declare|approve|reject|retire|revise|import|discover`);
  - an isolated, capability-checked Docker probe runner (`guard runner setup|probe`);
  - differential replay of the same witness in base and candidate (`guard verify ... --execute --run-profile NAME`);
- local open-weight contract proposals (`guard contracts discover --model local [--execute]`, `guard model status`):
  - bounded context packets;
  - an Ollama adapter restricted to a validated loopback endpoint;
  - strict parsing of model output, followed by the same validation, non-vacuity and falsification pipeline as template candidates;
  - model candidates are INFERRED at most and never evidence. Evaluated with qwen2.5-coder:7b only (see `benchmarks/results/m4_model_eval.json`);
- two more contract-proposer sources (user-requested extension, engine 0.5.0; DECISIONS I-105-I-109). Both feed the same validation, falsification and authority pipeline, and neither can produce more than INFERRED:
  - **bring-your-own-key remote models** (`guard contracts discover --model remote`): an OpenAI-compatible chat-completions adapter and an Anthropic Messages adapter. They are opt-in and never a fallback. They need https, `remote_allowed = true` and an `api_key_env` that names an environment variable; the key itself is never stored. **Context packets leave the machine**, and every run, contract, report and evidence record says where they went. Hosted providers were not exercised here (no key on this host);
  - **agent-proposed contracts**: the calling coding agent proposes. Use `guard contracts context SYMBOL --format json` and `guard contracts propose --file F`, or the MCP tools `get_context_packet` and `propose_contracts`;
- an agent interface (`guard mcp serve --stdio`, optional extra `mcp`):
  - eight tools over the same orchestration as the CLI: `verify_patch`, `check_scope`, `get_contracts`, `get_finding`, `explain_finding`, `get_context_packet`, `propose_contracts` (INFERRED only), `get_capabilities`;
  - root, path and preauthorized-profile restrictions; no approval, baseline or command tools;
  - cancellation, timeouts and stale-snapshot responses;
  - `guard explain FINDING_ID`;
  - a recorded repair loop (`examples/demo_repo/`) and a Claude Code `.mcp.json` example (`docs/MCP_INTEGRATION.md`). Alpha checklist: `docs/ALPHA_CHECKLIST.md`.

See `docs/IMPLEMENTATION_STATUS.md` for exactly what exists and what does not. There is no Kotlin adapter: M6 is deferred because this host has no JDK or Kotlin toolchain (feasibility note in `docs/DECISIONS.md` I-101). Other example commands in `docs/` describe the intended interface; they do not imply they are already implemented. The PDF is a rendering of the Markdown files as delivered with the kit.

Quick start (from this folder):

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.lock && .venv/bin/pip install -e . --no-deps
.venv/bin/guard check examples/python_demo/candidate --format json      # single snapshot: exit 1, validated witness
.venv/bin/python examples/patch_demo/run_demo.py                       # throwaway Git repo: Fixture D via guard verify
.venv/bin/python examples/contract_demo/run_demo.py --setup            # Fixtures B+E in Docker containers (--setup pulls python:3.13-slim explicitly)
.venv/bin/python examples/contract_demo/run_demo.py --backend unisolated  # same, explicitly unisolated on the host (no Docker)
.venv/bin/python -m pytest -q                                          # test suite (offline, no model; Docker tests skip if no daemon/image)
.venv/bin/python benchmarks/model_eval.py --repeats 3                  # M4 evaluation with a local Ollama model (installed separately; never downloaded by guard)
.venv/bin/guard mcp serve --stdio --root app=/abs/path/to/repo         # M5 MCP server (stdio; see docs/MCP_INTEGRATION.md)
.venv/bin/python examples/demo_repo/run_demo.py --out /tmp/ig-m5-run   # M5 repair loop: MCP + local model + Docker (recorded run in examples/demo_repo/recorded_run/)
.venv/bin/guard contracts context pkg/mod.py::fn --format json          # exact context packet + candidate schema for an agent proposer (contacts nothing)
.venv/bin/guard contracts propose --file batch.json --client my-agent/1.0 # agent candidates -> same pipeline; recorded INFERRED only
.venv/bin/guard contracts discover --model remote --dry-run            # opt-in remote proposer (see the [semantic] example below; sends context off-machine)
.venv/bin/guard capabilities --path .                                  # shows the configured proposer and whether its context leaves the machine
```

Remote proposer configuration (opt-in; `.guard/config.toml`). There is no default and no fallback in either direction:

```toml
[semantic]
enabled = true
provider = "anthropic"                 # or "openai_compatible" with base_url = "https://HOST/v1" (or "http://127.0.0.1:PORT/v1" for a local server)
model_id = "claude-sonnet-5-5"
api_key_env = "ANTHROPIC_API_KEY"      # the NAME of the environment variable; the key is never written to config, logs or reports
remote_allowed = true                  # required before any non-loopback destination is contacted
# base_url = "https://api.anthropic.com"   (default for provider = "anthropic")
```

**Privacy warning.** With a remote provider, bounded context packets are sent to that host. Each packet holds the target function, selected callers, callees and tests, and static facts. Provider terms apply. Free tiers in particular may retain submitted data or train on it. Use the local model or agent proposals when code must not leave the machine.

## File map

| File | Purpose |
| --- | --- |
| CLAUDE.md | Root-level implementation instructions and hard evidence rules |
| BUILD_START.md | Copyable first task, follow-up tasks and first-session definition of done |
| docs/01_PRODUCT_AND_SCOPE.md | Product thesis, users, release scope and boundaries |
| docs/02_ARCHITECTURE.md | Modules, data flow, repository layout and dependencies |
| docs/03_EVIDENCE_AND_POLICY.md | Proof, observation, provenance and gate semantics |
| docs/04_VERIFICATION_IR.md | Shared IR, explicit language semantics and frontend contract |
| docs/05_STATIC_ENGINE.md | Supported Python subset, analyses and solver behavior |
| docs/06_CONTRACT_ENGINE.md | Candidate discovery, typed property DSL and lifecycle |
| docs/07_INCREMENTAL_AND_BASELINES.md | Snapshots, impact analysis, invalidation and comparison |
| docs/08_LOCAL_MODELS.md | Open-weight reasoning, offline operation and model protocol |
| docs/09_INTERFACES_AND_STORAGE.md | CLI, MCP, report examples, configuration and persistence |
| docs/10_EXECUTION_AND_PRIVACY.md | Execution boundary, sandbox behavior and data handling |
| docs/11_ROADMAP_AND_GATES.md | Dependency-ordered milestones and release criteria |
| docs/12_TESTING_AND_RESEARCH.md | Semantic fixtures, benchmarks, ablations and reproducibility |
| docs/13_DEMO_AND_ACCEPTANCE.md | Exact demo fixtures and acceptance scenarios |
| docs/14_DECISIONS_AND_REFERENCES.md | Decision record, risks and primary-source reading |
| docs/15_TASK_BACKLOG.md | Ordered implementation tasks with concrete completion checks |

## Build sequence

M0 foundation -> M1 Python static CLI -> M2 patch impact and baselines -> M3 behavioral contracts -> M4 local semantic discovery -> M5 MCP -> M6 Kotlin -> M7 broader language and library adapters.

The first integrated product milestone is M5. Python static checks alone are an engineering checkpoint. They do not fulfill the whole project. Kotlin remains part of the planned beta; broader language support follows only after semantic adapter tests exist.

## Important interpretation

All timings, model sizes, supported-language counts and quality targets are proposed goals unless explicitly labeled as measured. No name, package identifier, trademark or license choice is finalized by this kit. No prior research code is included or assumed available.

A successful scan means selected checks completed without a blocking finding. It never means the entire repository is correct. An observed regularity remains a candidate until its evidence and specification authority are recorded.
