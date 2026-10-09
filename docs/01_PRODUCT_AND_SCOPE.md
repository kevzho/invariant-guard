# 01. Product and scope

## Thesis

A local-first verification layer for coding agents incrementally analyzes generated patches, discovers repository-specific behavioral contract candidates, checks modeled runtime-failure paths and contract changes, and returns concrete evidence directly into the repair loop.

The product primitive is `verify(base, candidate, policy) -> report`. A coding agent can call it repeatedly while editing. The user receives a task completion claim supported by bounded verification evidence rather than a code review opinion alone.

## Users and jobs

| User | Immediate job | Useful output |
| --- | --- | --- |
| Developer using an agent | Catch newly introduced failures before accepting a patch | Source span, path, witness and repair context |
| ML/data researcher | Preserve numeric, shape, schema and determinism properties | Valid-domain counterexample and baseline comparison |
| Maintainer | Keep repository-specific API behavior across refactors | Contract provenance and intentional-change workflow |
| Privacy-sensitive team | Verify without sending repository contents to a cloud model | Offline analysis and auditable local inference |

The initial user is a developer running an agent on a small Python project. Research and ML examples are useful demonstration cases, but the core is not specific to PLForecast, sports simulation or one repository.

## Product principles

Local operation is the default. Open-weight reasoning is a first-class layer with a pluggable adapter and a no-model fallback. Repository-specific evidence complements generic runtime-failure classes. The report distinguishes exact modeled proof, observed failure, likely issue, candidate property and unknown coverage. Patch comparison attributes differences without pretending all observed behavior is intended.

## Scope ladder

| Checkpoint | Real capability | Deliberate boundary |
| --- | --- | --- |
| M1 | Python static failure checks over a declared subset | Single snapshot; no learned dynamic contracts |
| M2 | Change impact, caching, before/after static differences | Conservative invalidation; no universal equivalence proof |
| M3 | DSL contracts and isolated differential probes | Execution must be configured; tests are finite evidence |
| M4 | Local model proposes semantic candidates | Model cannot approve contracts or certify safety |
| M5 | CLI/MCP agent repair integration | No claim of universal agent compatibility |
| M6 | Kotlin adapter and shared checks | Version-pinned JVM semantics initially |
| M7 | TS and selected library adapters, then other frontends | Coverage grows only after semantic conformance tests |

The full planned project includes Python, Kotlin, TypeScript and eventually additional languages, a shared verification core, local open-weight inference and agent integrations. M1 is a narrow step toward that project, not a replacement definition.

## Initial bug and contract classes

M1: division-by-zero feasibility for supported integer domains; basic reachability; explicit unknowns. M2 adds constant/interval index bounds and narrow None receiver flows. M3 adds nonempty-input normalization, numeric range, selected argument immutability and repeatability contracts. Kotlin beta targets integer division, array bounds and explicit null-assertion failures. Tensor shapes, DataFrame columns and JSON schemas require versioned library summaries and arrive later.

Resource leaks, concurrency races, arbitrary casts, exception-policy inference, network availability and whole-program equivalence are later research directions. No deadline is promised for them.

## Success criteria

A useful finding gives a property or failure class, affected scope, exact source span, evidence class, declared assumptions, a witness when available, baseline relationship, and a reproducible replay recipe when execution is enabled.

A useful successful report says which obligations were checked, which were safe within their assumptions, which contracts were tested, which checks were skipped and whether policy permits accepting the patch.

The first integrated release must include a real model-assisted contract discovery path, a no-model path, change-aware verification, evidence persistence and an actual CLI/MCP repair demonstration.

## Intent and intentional changes

The old implementation is evidence of prior behavior, not an oracle of desired behavior. Every learned contract has an authority state. Auto-mined properties default to advisory. A developer can accept a deliberate behavior change by revising an approved contract with a reason and linked patch. The verifier checks the revised contract separately; it never silently redefines success to match the patch.

## Performance goals

Target a warm static path below two seconds at p95 on a defined reference laptop and small-to-medium fixtures. Treat sub-second cases as a stretch goal. Use separate budgets for solver checks, replay and model inference. Publish hardware, workload, coverage and cold/warm measurements. No latency figure in this specification is a measured result.
