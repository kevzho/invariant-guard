# 03. Evidence and policy

## Separate the axes

Do not make one numerical confidence value stand in for proof, frequency, intent and severity. Every result carries these independent axes:

| Axis | Values or meaning |
| --- | --- |
| property_result | PROVED_SAFE, PROVED_UNSAFE, POSSIBLY_UNSAFE, TESTED, OBSERVED_FAILURE, OBSERVED_REGRESSION, UNVERIFIED, UNKNOWN |
| scope_validity | EXACT_MODEL, CONDITIONAL, OVERAPPROXIMATE, BOUNDED, EXECUTED |
| authority | DECLARED, APPROVED, INFERRED, REJECTED, RETIRED |
| patch_relation | INTRODUCED, PRE_EXISTING, RESOLVED, CHANGED, UNDETERMINED |
| severity | ERROR, WARNING, INFO, according to declared policy |
| coverage | supported obligations, unsupported constructs and skipped work |
| run_outcome | COMPLETE, INCOMPLETE, ERROR, CANCELLED |
| verdict | PASS, WARN, BLOCK, INCOMPLETE, ERROR |

A proof is a statement about one property under explicit assumptions. A gate verdict is a policy decision about a whole run.

## Exact meaning of result labels

**PROVED_SAFE:** the bad-state obligation is unsatisfiable in a sound supported model, within declared inputs, semantics and effects. Quantify the property and scope. This can prove only division safety for a function, not all safety or total correctness.

**PROVED_UNSAFE:** an existential bad-state path is satisfiable and the modeled execution is exact for that obligation. Emit the concrete witness and assumptions. This means a failure is possible for at least one admitted input, not that every invocation fails. An unvalidated hypothetical caller path cannot become an unconditional repository failure.

**POSSIBLY_UNSAFE:** a bad state is not excluded by an approximate model, external analyzer or incomplete path. A SAT result on an overapproximation may be spurious. State the imprecision.

**TESTED:** finite probes passed. Record counts, input domains, seeds, environments and failure-search strategy. Never equate no found counterexample with universal truth.

**OBSERVED_FAILURE:** an enabled execution produced a real failure. Record input and runner result. A timeout, isolation failure or import error in an incompatible environment is not automatically a defect.

**OBSERVED_REGRESSION:** the same valid-domain witness satisfies the baseline property and violates it in the patch under comparable pinned environments. Record both executions and property evaluation. For nondeterministic results record repeats and uncertainty; do not infer universal determinism from a handful of trials.

**UNVERIFIED:** a valid candidate exists but has no sufficient mechanical evidence.

**UNKNOWN:** unsupported semantics, solver unknown, incomplete scope, invalid assumptions or missing environment prevents establishing a result.

## Proof obligations and assumptions

A type annotation in Python is a proposed domain restriction, not runtime enforcement. A proof over annotated integer inputs must explicitly say it is conditional on exact built-in integers. For a caller using `isinstance`, consider bool and subclasses; exact built-in semantics may require `type(x) is int` or a declared trusted boundary.

Facts derived only from samples or a local model may not become proof assumptions silently. Distinguish declared preconditions, sound static facts, test observations and guesses. If an operation can call user code through overloads, its numeric rule is conditional or unknown.

For an SMT safety query use `path_conditions AND error_condition`. UNSAT is useful only when the path model and domain cover the specified scope soundly. SAT establishes modeled feasibility only when the relevant modeled path is exact; otherwise it is a possible issue until independently validated.

Bounded unrolling can establish a witness within its modeled bound. A no-failure result within that bound must remain explicitly BOUNDED; it cannot prove safety of an arbitrary loop. In the first release unsupported loops return UNKNOWN instead of being silently truncated.

## Evidence record minimums

Every evidence item stores schema version, engine version, run ID, snapshot ID, symbol ID, property ID, result label, domain, assumptions, precision, source span and creation time. Solver evidence additionally stores solver version, query digest, response, optional witness and limits. Execution evidence stores run profile digest, interpreter/compiler identity, environment lock digest, inputs, outputs/exceptions, isolation capability and replay artifacts.

Immutable evidence can be superseded by a newer result but must not be rewritten. Properties and source identities can change; history references the exact revision checked.

## Default gate policy

| Condition | Default action |
| --- | --- |
| Introduced exact modeled runtime failure under validated or explicitly admitted domain | BLOCK |
| Introduced conditional failure whose preconditions are unconfirmed at real callers | WARN; offer witness validation |
| SAT on overapproximate model | WARN; never label as proof |
| Replayed regression of DECLARED or APPROVED contract | BLOCK |
| Change to INFERRED property | WARN; ask maintainer to review intent through contract workflow |
| Pre-existing defect with no demonstrated widening | Report separately; no new patch block by default |
| Pre-existing defect with newly feasible path or broader violating domain | CHANGED finding; evaluate the changed portion |
| Required obligation unsupported, skipped or timed out | INCOMPLETE |
| Optional analysis unavailable | WARN plus capability disclosure |
| Engine/internal failure | ERROR |
| Required work completed, no blocking findings | PASS or WARN based on advisories |

An approved contract regression can be intentionally accepted by the user through contract revision, not by deleting evidence. A static failure in a supposedly required input domain is not waived by silently narrowing the domain.

## Aggregation

Verdict precedence: ERROR, BLOCK, INCOMPLETE, WARN, PASS. Preserve run_outcome separately, so a BLOCK report can also be incomplete. CLI BLOCK takes precedence over incomplete but includes both reasons. Required coverage must be defined in the run plan before analysis begins; do not redefine it afterward to manufacture PASS.

## Examples

`ratio(a, b)` has an exact integer-model witness `a=1, b=1` for a zero denominator. With only annotations, scope_validity is CONDITIONAL and callers must be evaluated before a repository-level unconditional claim.

A model proposes `output >= 0`; 5,000 probes pass. Result is TESTED, authority INFERRED, quantifier finite sampled inputs. It remains advisory.

A model proposes a probability bound, but a caller passes arbitrary plugin objects. Without modeled overloads the static result is UNKNOWN. The proposal can still be saved as UNVERIFIED.

No field called `confidence: 0.998` is needed. Measured precision belongs in aggregate evaluation reports with denominators and uncertainty, not as a fake posterior on one contract.
