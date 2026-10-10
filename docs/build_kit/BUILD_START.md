# Start building with Claude

## Paste this as the first task

Read CLAUDE.md and the specifications it names. Implement M0 and M1 of InvariantGuard in this repository. Start by stating the supported Python subset and the module boundaries. Create the package, typed result and IR models, AST frontend, interval/path analyzer, optional Z3 integration, and a working static CLI.

Make the guarded and unguarded ratio fixtures from docs/13_DEMO_AND_ACCEPTANCE.md work through actual analysis. Add meaningful tests for branch reachability, unsupported semantics, source mapping and JSON/exit behavior. Static checks must not import or execute the target repository. Report incomplete coverage honestly.

Run the relevant checks, update docs/IMPLEMENTATION_STATUS.md, and leave a runnable Python static-check vertical slice. Stop at the M1 acceptance gate with the implementation status and exact commands. Do not simulate future features or claim the full product is finished.

## First-session decisions

Use Python 3.11 or newer for the host package; select and document one tested interpreter version. Use stdlib AST for Python parsing, dataclasses plus strict serialization/schema validation, argparse for the first CLI, pytest for tests, and Z3 as an optional solver dependency. Choose exact dependency versions during implementation and lock them.

Keep `guard` as the desired local executable name. Use `invariantguard` as the source package. Check public package and command naming before any public release; the working name is provisional.

Treat the specification as version 1.0. If details conflict, follow evidence and language semantics rules first, then record a small decision with the rationale. Do not silently redesign the whole system.

## M1 first-session completion

- `guard check PATH --format json` analyzes supported Python without importing it.
- Unsafe ratio includes a satisfiable exact modeled path and an integer witness.
- Guarded ratio proves the division obligation safe in its declared integer domain.
- Unknown call handling demonstrates honest incomplete coverage.
- JSON is schema-valid and stable; diagnostics go to stderr.
- Tests run on CPU with no network or local model.
- An engine failure and an unsupported operation are different outcomes.
- The status file identifies every implemented and deferred capability.

## Follow-up tasks

### M2

Implement immutable base/current snapshots, patch-to-symbol mapping, conservative dependency invalidation, summary caching and differential static comparison. Include changed-callee/unchanged-caller, untracked files, deletions and rename fixtures. Measure warm and cold runs separately.

### M3

Implement the typed contract DSL, immutable evidence records, baseline/candidate authority states and an explicitly enabled isolated probe runner. Reproduce a true normalization regression on the same valid input in both snapshots. Keep finite tests distinct from proofs.

### M4

Implement the local model adapter protocol and candidate schema. Integrate one real open-weight model hosted on the developer's machine, with bounded context and no external traffic. Mechanically check every proposed property. Record model identity and test invalid/adversarial suggestions.

### M5

Implement a stdio MCP server over the same orchestration API as the CLI. Demonstrate verify -> evidence -> agent repair -> verify with an actual client configuration. Return coverage and assumptions, not only a verdict.

### M6

Integrate Kotlin through a typed adapter, initially targeting a documented Kotlin/JVM compiler version. Validate integer division, array bounds and null assertions against isolated compiled fixtures. Reuse the core report, policy and contract schemas.

## Progress record

Create `docs/IMPLEMENTATION_STATUS.md` during M0 with: active milestone, supported semantics, completed tasks, validation commands, measured results, known gaps and next task. Do not fill it with speculative results.
