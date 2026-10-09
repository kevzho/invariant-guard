# Claude implementation instructions

You are implementing InvariantGuard from the specifications in this folder. Read `README.md`, `BUILD_START.md`, `docs/03_EVIDENCE_AND_POLICY.md`, `docs/11_ROADMAP_AND_GATES.md` and the tasks for the active milestone before changing code. Read the relevant technical specification before building its module.

## Product definition

Build a local-first, incremental verifier for coding-agent patches with two coordinated engines: modeled runtime-failure analysis and repository-specific behavioral contract verification. Add a local open-weight model as a contract proposer. Keep CLI and MCP results machine-readable so another coding agent can repair a patch from the returned evidence.

## Work rules

- Implement in dependency order. Begin with M0 and a real M1 vertical slice.
- Choose Python for the orchestration/core prototype and first frontend. Implement Kotlin second through a versioned adapter. Do not start by building eight parsers.
- Use a minimal typed Verification IR from the beginning. Extend it only when a shipped analysis needs another operation. Do not build a compiler optimization framework.
- Preserve actual language semantics. Operators sharing syntax can have different failures. Unsupported behavior must propagate uncertainty.
- An LLM response, a type annotation, a passing sample or a solver model of an approximate program is never sufficient for an unconditional proof.
- Keep proof status, execution evidence, coverage, authority and severity separate. Follow the schemas and gate rules.
- Use real source analysis and real test execution when enabled. Do not hard-code the demonstration findings or use regex as the final Python frontend.
- Implement the no-model path. Make open-weight reasoning a real optional runtime capability, not a stub advertised as complete.
- Use a typed property DSL. Do not `eval` candidate strings or execute arbitrary model-produced code.
- Static commands must never import the repository, install dependencies, run its hooks, or execute its tests.
- Dynamic checks require the explicit run configuration described in the execution specification. A subprocess is not an isolation sandbox.
- Capture immutable snapshots for every verification. Abort comparison or mark it incomplete if the repository changes during the run.
- Track cross-file impact. An unchanged caller can become unsafe when a callee changes.
- Intentional API changes can revise approved contracts through an explicit, auditable change. Never automatically accept a changed baseline just to silence a failure.
- Never change the repository being checked. Never auto-repair user code, reset branches or discard uncommitted files.
- Keep network use out of verification by default. Loopback inference is allowed only for a configured local endpoint.
- No model downloads during scans. No cloud fallback. No telemetry by default.
- Pin tested dependencies in a lock file; record solver, interpreter and adapter versions. Use documentation for the exact versions selected.
- Update task checkboxes only when their acceptance checks pass. Leave unsupported features documented and unimplemented rather than inventing evidence.
- Use small reviewable changes. Run checks appropriate to each change. Record commands and outcomes in `docs/IMPLEMENTATION_STATUS.md`.

## Evidence requirements

Use `PROVED_SAFE` and `PROVED_UNSAFE` only for the declared property, modeled semantics, quantifier, assumptions and scope. A bounded search must say bounded. A satisfiable overapproximation yields a possible failure, not a proven real path. Passing property tests yields `TESTED`, not proof.

A observed regression must include the same witness run against the baseline and patch, environment identities, and a confirmed baseline pass plus patch failure. If baseline already fails, label it pre-existing. If baseline does not run, comparison is unknown.

A scan with missing required coverage is `INCOMPLETE`, even if no defects were found. An engine crash is `ERROR`. Neither becomes `PASS`.

## First implementation target

Build the two `ratio` examples in `docs/13_DEMO_AND_ACCEPTANCE.md` end to end: AST -> typed IR -> interval/path reasoning -> optional solver -> real source location -> stable JSON -> CLI exit code. Include the safe guard case and the unsupported-call case.

Do not implement local models, dynamic importing, Kotlin, a web dashboard or large language support before this path works.

## Definition of done for any milestone

Working implementation, meaningful semantic fixtures, no fabricated outputs, documented supported subset, accurate uncertainty propagation, repeatable commands, and an updated status file. If a prerequisite is unavailable, implement the independent parts and record the concrete remaining blocker.
