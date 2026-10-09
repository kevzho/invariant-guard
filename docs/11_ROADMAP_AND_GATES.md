# 11. Roadmap and release gates

## Build order and estimates

The order below is dependency-driven. Effort ranges are planning estimates for one experienced builder with focused implementation time; they exclude unknown research-code integration and are not calendar promises. Milestone gates matter more than dates.

| Milestone | Scope | Estimated focused effort | Depends on |
| --- | --- | --- | --- |
| M0 | Package, schemas, fixtures and status record | 2-4 days | Nothing |
| M1 | Python static vertical slice | 1-2 weeks | M0 |
| M2 | Impact, baselines, caches and added checks | 1-2 weeks | M1 |
| M3 | Contracts, isolation and differential probes | 2-3 weeks | M2 |
| M4 | Real local model discovery | 1-2 weeks | M3 typed DSL |
| M5 | MCP and measured agent repair demo | 1 week | M2-M4 |
| M6 | Kotlin/JVM adapter beta | 2-4 weeks | M1 IR; M5 integration |
| M7 | TS/library adapters and broader support | Open-ended | Validated earlier releases |

Work can be interleaved when interfaces are stable, but no gate should be declared complete using unimplemented capabilities. This plan does not require parallel agents.

## M0: foundation

Deliver source package, test setup, schema validation, provisional command entrypoint, fixture manifests, first decision record and IMPLEMENTATION_STATUS.md. Schemas include assumptions, coverage and authority from the beginning.

Gate: package installs in a clean local environment; schema invalid/unknown cases fail validation; tests run without model/network; source maps and snapshot identity are represented.

## M1: single-snapshot static loop

Implement AST-to-IR lowering, integer domains, CFG, intervals, nonzero/path facts, division obligations, optional SMT and structured CLI. Add supported/unsupported coverage reporting.

Gate: the safe/unsafe/unknown fixtures produce actual semantic outcomes; no repository import occurs; solver unknown and engine error are distinct; a conditional domain never becomes unconditional through annotations.

Demonstrate a concrete witness for unsafe ratio and a guarded safe obligation. Do not claim patch attribution before M2.

## M2: patch verification

Add explicit snapshots, Git/non-Git baselines, diff-to-symbol mapping, dependency graph, conservative slices, summary invalidation, before/after static comparison, basic list bounds and narrow None checks.

Gate: unchanged callers are rechecked when a callee changes; new/untracked/deleted files participate; incremental and full required obligations agree on fixtures; stale caches fail closed; no checkout or working-tree mutation occurs.

Measure static timing and memory. Capture unsupported dynamic edges rather than hiding them.

## M3: behavioral contracts

Implement typed predicate/input DSL, lifecycle/authority, immutable evidence, deterministic template discovery, isolated runner, baseline replay, shrinking and approved-contract policy.

Gate: normalize regression demonstrates baseline pass/candidate fail on the same positive-sum input; invalid-domain and both-fail cases are not regressions; passing probes say TESTED; runner unavailable means incomplete when dynamic checks are required.

At this gate, the core dual-engine architecture exists. Contracts may be declared or template-mined; model inference is still pending.

## M4: open-weight semantic layer

Implement local adapter, bounded context, structured candidate validation, candidate falsification and hardware/model evaluation. Configure one actual open-weight artifact; record its tested license and runtime identity.

Gate: real model-generated candidates enter the same mechanical pipeline as deterministic candidates; invalid/vacuous/adversarial suggestions are rejected; no cloud/network fallback occurs; model-off remains functional.

This gate is necessary for the full local/open-weight product thesis. A mock model response is useful for unit tests but cannot satisfy release acceptance.

## M5: agent-native alpha

Implement stdio MCP, shared APIs, cancellation and compact evidence retrieval. Document one tested client integration and demonstrate patch -> verify -> failure evidence -> repair -> verify.

Gate: the report tracks the final snapshot; no stale evidence is attributed to the repaired code; unknown required work prevents PASS; the agent never auto-approves a baseline/contract change. Measure the complete loop separately from verifier latency.

A hook or CI integration can enforce acceptance where the client supports it. Report client conventions accurately; MCP alone is not enforcement.

## M6: Kotlin beta

Run the compiler-adapter feasibility spike. Pin Kotlin/JVM/compiler settings. Implement typed lowering and integer/bounds/null-assertion checks, then reuse patch scope, evidence and interfaces.

Gate: Kotlin fixtures match compiled isolated executions; overflow and null semantics are not translated as Python arithmetic; adapter version changes invalidate summaries; no automatic arbitrary Gradle execution.

If research code is supplied later, evaluate reuse at this milestone. The plan does not imply its source, sponsor permission or dependencies are already available.

## M7: expansion

TypeScript first where user demand supports it, including undefined propagation, Number/BigInt distinctions and Promise-related checks with documented semantics. Add versioned tensor-shape, DataFrame-schema and JSON-contract models after small controlled fixtures.

Only add a new language when its typed adapter and semantic fixtures pass. Java/Go/Rust/C/C++ are architecture directions, not simultaneous launch commitments.

## Release criteria

Before an alpha: M0-M5 gates, working no-model path, actual local model path, supported-subset docs, schema-valid reports, replayable regression, offline operation and one real client repair integration.

Before Kotlin beta: M6 gate plus shared-schema compatibility and performance/coverage regression checks.

Before public package release: check naming, choose source license, audit shipped dependency/model licenses, document limitations and install commands, create a clean versioned release. No publishing is authorized merely by reading this kit.

## Reduce scope if blocked

If SMT support is difficult, ship interval/path results with UNKNOWN on unresolved queries while continuing solver work. If isolation is missing, keep static verification useful but do not mark M3 dynamic acceptance complete. If model hardware is weak, use a smaller evaluated open-weight model or no-model mode; do not substitute an undisclosed cloud call. If Kotlin compiler internals are unstable, pin the adapter or postpone the gate transparently.

Do not reduce scope by dropping assumptions, coverage or provenance. Those are core product behavior.
