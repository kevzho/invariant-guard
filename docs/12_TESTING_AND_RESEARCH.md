# 12. Testing, benchmarks and research

## Validation layers

| Layer | What it establishes |
| --- | --- |
| Unit semantics | Correct transfer rules, lattices, DSL typing and policy |
| Language conformance | IR/model behavior agrees with isolated language fixtures |
| Static integration | Real source reaches source-mapped report without imports |
| Differential replay | Baseline/candidate attribution uses comparable observations |
| Incremental equivalence | Cached/sliced results preserve required full-run obligations |
| Agent integration | A real client consumes evidence and repairs a real patch |
| Privacy/isolation | Actual default operation matches local/execution claims |
| Research benchmark | Measured utility and latency on held-out tasks |

Do not test only that implementation constants repeat themselves. Each core rule needs a meaningful counterexample or boundary case.

## Fixture manifest

Every case contains case ID, language/version, baseline source, patch source, property/domain, expected result and scope, ground-truth method, environment lock, seed and origin/license. Include clean safe cases and unsupported cases as well as defects.

Initial Python suites: unguarded/guarded division, impossible branch, annotation-only domain, unknown external call, direct caller precondition, callee change, Python negative index, empty list, None flow, mutable alias, NaN/float unsupported behavior, ignored assertion runtime mode, syntax error and mid-run edit.

Dynamic suites: normalization positive-sum regression, all-zero excluded domain, both versions already fail, purity regression, controlled repeatability, shrinking preserves domain, missing baseline environment and isolation denial.

Kotlin suite: Int division, overflow-sensitive path, array index, explicit `!!`, safe null guard and unresolved Java platform type.

## Prevent fabricated precision

Report TP/FP/FN only for cases with independently established labels. Maintain safe negatives. Count unknowns and abstentions instead of removing difficult cases from the denominator.

A static function-domain witness is not necessarily a reachable application defect. Ground truth should record this distinction. A model-inferred property can be mechanically valid but undesired; developer intent review is a separate label.

## Benchmark tracks

1. Controlled semantic fixtures: exact modeled safety, failures and unknown behavior.
2. Seeded realistic patches: hand-written changes in small repository examples, with independent tests.
3. Real agent patches: versioned tasks and baseline repositories, with disclosed generation and evaluation environment.
4. Repository contract utility: blind review of inferred properties and found counterexamples.
5. Incremental performance: full/cold/warm runs under the same required coverage.

Synthetic defects prove engineering behavior. They do not alone establish effectiveness on natural coding-agent mistakes.

## Metrics

Precision/recall by failure class and domain; conditional/reachable distinctions; abstention rate; required-obligation resolution; incremental/full mismatch count; p50/p95 wall time; peak memory; cache hit/invalidation counts; model schema/checkable/accepted-candidate rates; replay availability; counterexample minimization; repair success and attempts; false blocks on intentional changes.

Separate total verifier time into capture, parsing, impact, abstract analysis, solver, dynamic checks and model. Warm static time excludes asynchronous discovery only when clearly labeled. End-to-end latency includes what the user actually waits for.

## Proposed performance experiment

Use a reference laptop with disclosed OS, CPU, RAM, interpreter and model hardware. Test repository scales with approximately 100, 1,000 and 10,000 supported functions and changes spanning one, several and many dependency nodes. Include one highly connected global change.

Use at least 30 repeated timings per condition where feasible, recording cold/warm definitions. Run full and incremental modes at the same coverage. A two-second p95 quick-static target is provisional; publish measured outcomes rather than rewriting the target as achieved.

## Baselines and ablations

Compare:
- generic static analyzer adapter alone;
- InvariantGuard static full versus incremental;
- declared contracts versus deterministic inferred templates;
- local model candidates plus mechanical verification;
- static-only, dynamic-only and combined verification;
- full dependency invalidation versus weaker experimental slicing.

Use REDO and agentic property-based testing as conceptual prior work. A direct benchmark requires reproduced code, compatible tasks and documented settings; do not put imaginary performance numbers into a competitor table. Licensing and language differences matter.

## Research questions

Does conservative change-aware verification preserve useful detection while reducing latency? Do mechanically checked local-model candidates add useful repository properties beyond templates? Does baseline differential evidence reduce false blocks? How often does combining static feasibility and dynamic falsification improve actionable findings? What are the costs of honest abstention?

These are hypotheses, not established novelty or effectiveness claims.

## Reproducibility

Lock code/dependency revisions, semantic profiles, compiler/solver/model artifacts, prompts, generation parameters, input seeds, environment manifests and benchmark splits. Version contracts and baseline identities. Store raw outcomes where retention permits.

Use held-out repositories split by repository family to reduce leakage. Do not tune rules on test results and then report the same cases as held out. Bootstrap intervals by repository when clustering matters. Report sample counts and finite uncertainty; no unearned per-finding probability.

## Initial research deliverables

A documented defect/contract fixture set, measured cold/warm report, model-candidate evaluation, incremental/full consistency results and a recorded agent repair demo. A paper comes after reproducible empirical evidence, not before a benchmark exists.
