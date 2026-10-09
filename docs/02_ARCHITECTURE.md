# 02. Architecture

## End-to-end pipeline

1. Resolve repository root and explicit base/current snapshot identities.
2. Read source bytes without importing repository modules.
3. Parse supported files and lower typed, source-mapped Verification IR.
4. Map changes to symbols, dependencies, contracts and library models.
5. Compute a conservative affected slice and invalidate stale summaries.
6. Run cheap static transfer rules; escalate supported uncertain obligations to SMT within budgets.
7. Retrieve approved and advisory contracts; optionally propose new candidates through local inference.
8. Mechanically check DSL properties statically where possible and dynamically only when execution is enabled.
9. Compare baseline/candidate evidence on the same scope and witnesses.
10. Persist immutable evidence, evaluate gate policy and return structured results.

Contract discovery can run as a background or explicit task. A quick verification reads existing contracts without invoking a model. Re-discovery must not block every agent edit.

## Proposed source tree

```text
src/invariantguard/
  cli.py
  orchestration.py
  models/          # versioned reports, evidence, contracts and source spans
  snapshots/       # immutable source manifests; optional Git reader
  frontends/
    python/        # AST, symbol resolution, lowering and semantics profile
    kotlin/        # future version-pinned typed adapter
  ir/              # typed expressions, control-flow and effects
  analysis/        # intervals, null states, local flow and summaries
  solver/          # supported obligation encodings and budgets
  contracts/       # DSL validation, discovery, lifecycle and checking
  impact/          # symbol/dependency graph and invalidation
  execution/       # explicit runners and isolation capability checks
  semantic/        # local model protocol and structured candidates
  storage/         # SQLite metadata and content-addressed artifacts
  policy/          # finding classification and verdict aggregation
  mcp_server.py    # optional extra; shared orchestration entrypoints
tests/
  unit/
  semantic_fixtures/
  integration/
  differential/
benchmarks/
  fixtures/
  manifests/
examples/
  python_demo/
  kotlin_demo/
docs/
```

Use interfaces between modules rather than making an LLM adapter call the solver or runner directly. Only orchestration coordinates evidence production and applies policy.

## Internal interfaces

```text
Frontend.lower(source_snapshot) -> ModuleIR + Coverage
Analyzer.check(module_ir, scope, summaries) -> ObligationResults
Impact.plan(base_graph, candidate_graph, changes) -> AnalysisPlan
ContractEngine.validate(candidate) -> TypedContract or Rejection
ContractEngine.check(contract, snapshot, plan) -> Evidence
SemanticAdapter.propose(context_packet) -> CandidateBatch
Runner.replay(probe, snapshot, run_profile) -> ReplayResult
Store.append_evidence(evidence) -> ContentId
Policy.evaluate(results, coverage, policy) -> Verdict
```

Each call carries a run ID, cancellation token, resource budget and schema/engine identity where relevant. Errors are typed. A runner timeout is not a program failure.

## Technology choices

Start with Python orchestration, stdlib AST, dataclasses, explicit JSON schemas, SQLite and optional Z3. Use pytest for semantic and integration tests. Add property-based tooling only when dynamic contracts are implemented. Use the official MCP SDK as an optional dependency after the CLI API stabilizes.

Do not select a default local model from an unverified name or size mentioned in earlier brainstorming. In M4, evaluate available open-weight models for structured contract generation on the actual development hardware, pin one tested artifact and license, and document it. Model integration should support a configurable loopback endpoint.

Kotlin adapter choices require a small feasibility spike: compiler FIR/IR extension versus a typed sidecar using supported compiler APIs. Compiler internals can change. Prefer a reproducible pinned sidecar boundary; do not import an existing research plugin without access, compatibility and license review.

Tree-sitter is optional later for fast syntax indexing. It does not replace type resolution, CFG construction or language semantics.

## Reuse and separation

Use existing parsers, compiler frontends, solver and property-testing libraries. Build the integration logic: patch scope, contract provenance, evidence types, typed candidate validation, policy and agent-facing counterexamples.

An external analyzer can contribute findings through an adapter but cannot bypass evidence classification. Tool output retains its version, rule ID, trust level and precision limits.

## Dependency boundaries

The core should import neither MCP nor model clients at startup. `guard check` must work without a model, network, dynamic runner or compiler sidecar. Missing optional dependencies produce a capability status and actionable error only when the user requests that capability.

## Atomicity and concurrency

Multiple runs may read snapshots concurrently. Use SQLite transactions for metadata and immutable artifact IDs for results. Finalize a run only after its expected evidence is committed. Invalidate unfinished runs after interruption; do not reuse them as passing cache entries. Never have background discovery mutate the authority state of approved contracts.
