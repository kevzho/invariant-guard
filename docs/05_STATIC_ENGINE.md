# 05. Static engine

## M1 supported subset

Support module-level named Python functions containing scalar parameters, exact built-in integer literals, assignments, integer addition/subtraction, simple multiplication where sound, comparisons, if/else, early return and division operators. Domains may be explicitly declared or inferred from exact literals. Annotated domains remain conditional.

Reject or mark relevant coverage unknown for decorators, arbitrary objects, overloaded operations, unknown calls, exceptions, loops, comprehensions, generators, async, reflection and dynamic imports. Do not parse only the easy expressions and present the whole function as verified.

M2 can add fixed built-in lists/tuples, modeled lengths/indexes and narrow None-flow checks with exact receiver types. Further support is explicit capability expansion.

## Analysis order

1. Validate syntax and supported semantic domain.
2. Build CFG and symbol/value mapping.
3. Propagate constants and sound intervals to a fixpoint for supported acyclic flow.
4. Refine branch facts and nonzero/None states.
5. Collect obligations at risky operations.
6. Resolve cheap definite cases before constructing solver queries.
7. Encode supported remaining queries with a per-query time limit.
8. Interpret solver SAT/UNSAT/UNKNOWN using precision and coverage rules.
9. Emit source-mapped results and unsupported obligations.

Intervals need top, bottom, finite/infinite bounds, join, intersection and arithmetic transfer. Track nonzero constraints separately because a single interval cannot express a gap around zero. A reachable join unions facts; it must not preserve facts true on only one branch.

## Division

For each supported `/`, `//` or numeric `%`, ask whether the divisor can be zero on a feasible path. Focus M1 on zero-denominator failures, not full arithmetic equivalence.

- Divisor interval excludes zero: safe for this obligation under domain assumptions.
- Constant zero on a reachable exact path: unsafe.
- Interval includes zero: uncertain; do not declare unsafe solely from the interval.
- Affine path over declared exact integers: optional SMT query.
- Unknown overloaded operation: unknown semantics.

Encode supported integer addition/subtraction and comparisons with mathematical integers only for the Python bigint profile. For fixed-width Kotlin integers later, use bitvectors and the correct signed-operation semantics.

An `assert` is not automatically a trusted runtime guard because Python can disable assertions. Record runtime mode or use explicit guarded control flow in proof fixtures.

## Bounds

For built-in Python sequence length `n`, an index `i` is valid when `-n <= i < n`. Empty sequences admit no integer index. Do not apply this condition to slices, dictionaries, NumPy objects or user-defined `__getitem__`.

Model only known built-in sequence semantics and length facts. An unknown call that can mutate a list invalidates its length summary. Bounds through aliases are unknown until alias/effect support is sound.

## None flows

Track None, definitely non-None and unknown as a small lattice. Attribute access on None can fail, but exact exception behavior of arbitrary receivers remains unknown. Treat `if x is not None` as a refinement. Mutating callbacks can invalidate object facts.

Do not conflate Kotlin nullable types, TypeScript undefined and Python None into one universal dereference rule.

## Interprocedural summaries

M2 begins with direct named calls within a resolved local module. A summary describes declared/required domain, possible return values, modeled failures and effects. A proof at a caller is conditional on the summary preconditions; verify those preconditions.

Calls through unresolved names or dynamic references widen the result and effects. Recursive strongly connected components initially use conservative unknown summaries; record full coverage gaps. Later add widening and fixed-point summaries with explicit bounds.

## Solver boundary

Use an optional Z3 adapter that builds typed expressions, stores query digests and translates supported concrete witnesses to JSON. Never allow model-provided SMT text into the solver unchecked. Enforce solver timeout and cancellation; track SAT, UNSAT and UNKNOWN separately.

Default design budgets: about 100 ms per simple query and 2 seconds for the quick static run, configurable and subject to measurement. Exceeding a required budget produces incomplete coverage. These are proposed defaults, not performance claims.

For exact modeled SAT, check the witness against the typed IR evaluator as an additional consistency check. The evaluator is not proof of frontend correctness; semantic conformance tests validate that boundary. For approximate SAT, runtime replay can establish an observed failure if enabled, otherwise keep POSSIBLY_UNSAFE.

## Differential attribution

Compare obligations in base and candidate snapshots. A diagnostic identity alone does not determine introduction: a changed denominator or caller precondition can expand a pre-existing failure domain. Prefer replaying the same static witness in both modeled versions. If matching is ambiguous, relation is UNDETERMINED.

Report both function-domain failures and reachable-call evidence. A helper admitting all integers may have a failure that a verified caller excludes. Do not claim repository-level reachability without proving or observing it.

## Required adversarial fixtures

Guarded divisor; unreachable bad branch; two-variable equality; unknown external call; Python bool/subclass domain issue; negative list index; empty list; alias mutation; widened caller domain; exception handler; syntax error; solver timeout; solver unavailable; unsupported float/NaN semantics.

A solver is an escalation mechanism, not a reason to encode every operation as unconstrained mathematical arithmetic.
