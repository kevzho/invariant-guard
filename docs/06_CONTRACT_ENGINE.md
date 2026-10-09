# 06. Contracts and specification discovery

## What a contract is

A contract is a typed predicate with a scope, precondition/input domain, authority state and evidence history. It can describe return ranges, length relations, selected purity/mutation behavior, serialization round trips, repeatability or other supported properties.

Repository-specific contracts are central to the project. Their intent must not be inferred solely from the fact that old code happens to satisfy them.

## Candidate sources

Read explicit configuration, assertions, tests, docstrings, types, direct call sites and local model proposals. Runtime traces are optional evidence produced by explicitly configured execution; never inject hidden instrumentation into a user's application.

Preserve which source suggested which claim. Tests/docs can disagree. A test is evidence about tested cases; a documented contract becomes DECLARED only when an explicit adapter/configuration maps it to the typed predicate. Do not turn natural-language guesses into an approved specification automatically.

## Lifecycle

```text
CANDIDATE -> VALIDATED -> CHECKED -> REVIEWED
                |           |
             rejected    counterexample / unknown / evidence
```

Authority evolves independently: INFERRED -> APPROVED or REJECTED; a maintainer may also DECLARE a contract directly. Approved changes produce a new revision; obsolete contracts become RETIRED. A proof can establish that a candidate holds within a domain without establishing that it is desired behavior.

## Minimal typed property DSL

Represent expressions as typed JSON nodes, not executable strings. v1 nodes:

- references: `arg(name)`, `result`, `before_arg(name)`, `after_arg(name)`;
- scalar operations: `literal`, `eq`, `lt`, `le`, `and`, `or`, `not`;
- controlled built-in operations: `len`, `sum`, `all_finite`, `approx_eq`;
- bounded collection predicates: `all_between`;
- relation templates: `unchanged_argument`, `repeatability`, later `round_trip`.

Each node has a declared accepted type and operation budget. `approx_eq` requires absolute/relative tolerance and explicit NaN/Infinity handling. A contract involving mutable arguments must specify copy strategy and supported serialization. Cycles, custom objects and equality methods remain unknown unless a trusted adapter exists.

Do not let a predicate call arbitrary repository code. A future round-trip template uses named allowlisted targets managed by the runner, not arbitrary expression execution.

## Example contract

```json
{
  "schema_version": "1.0",
  "contract_id": "normalize.sum.v1",
  "scope": "demo/math_ops.py::normalize",
  "authority": "APPROVED",
  "precondition": {
    "kind": "and",
    "args": [
      {"kind": "gt", "left": {"kind": "len", "value": {"kind": "arg", "name": "xs"}}, "right": {"kind": "literal", "value": 0}},
      {"kind": "all_between", "value": {"kind": "arg", "name": "xs"}, "lower": 0.0, "upper": 100.0},
      {"kind": "gt", "left": {"kind": "sum", "value": {"kind": "arg", "name": "xs"}}, "right": {"kind": "literal", "value": 0.0}}
    ]
  },
  "predicate": {
    "kind": "approx_eq",
    "left": {"kind": "sum", "value": {"kind": "result"}},
    "right": {"kind": "literal", "value": 1.0},
    "atol": 1e-9,
    "rtol": 1e-9,
    "require_finite": true
  },
  "input_profile": "bounded_builtin_numeric_list_v1",
  "provenance": [{"kind": "maintainer_approval", "reference": "contract-review-1"}]
}
```

Implement `gt` as a strict comparison node alongside `lt/le`, or canonicalize it during validation. Only these declared operations are accepted. The input profile also restricts lists to nonempty finite built-in numbers; JSON alone is not a complete generator.

## Candidate validation

Verify schema version, known symbol, types, allowed operations, resource limits, noncontradictory domain and provenance. Check that the precondition has at least one admissible witness. Reject vacuous properties and missing domains. Treat an unsatisfiable precondition as INVALID_CONTRACT, not a proof of success.

Static checking uses only operations the analyzer faithfully models. Dynamic checks use the exact approved predicate evaluator and input profile. Unsupported predicate nodes yield unknown rather than best-effort evaluation.

## Discovery and falsification

Start with deterministic templates: ranges, nonempty/length, explicit assertions and allowlisted purity contracts. The local model adds semantic candidates from bounded context packets. It must emit structured nodes, preconditions, source references and rationale.

Attempt falsification using boundaries, hand-authored inputs, existing examples and seeded property-based generators. Minimize a found counterexample within the valid domain. Never erase relevant preconditions during shrinking.

Keep discovery and evaluation sets separate. More observations can improve evidence coverage, but cannot justify universal numeric confidence or automatically approve intent.

## Baseline and regression checks

For every candidate witness:
1. Validate the input against the same contract domain.
2. Run the baseline in its pinned environment.
3. Run the candidate in the corresponding environment.
4. Evaluate the same contract revision against both.
5. Classify baseline pass + candidate fail as regression.
6. Classify both fail as pre-existing or invalid specification.
7. Classify missing/incompatible baseline as UNDETERMINED.

A normalization failure on all-zero input is not a regression if the baseline also divides by zero. Use a positive-sum input in the demo.

## Contract changes and stale evidence

Changing predicate, tolerance, domain, target symbol or library model invalidates the previous evidence for the new revision. Keep the old evidence as history. Deleted or renamed targets require explicit mapping or retirement. A model may suggest a revision but cannot approve it.

## Determinism and mutation

Repeatability contracts define seed, number of repeats, environment controls and output comparison. Passing repeats provide TESTED evidence. A patch failure should be repeated to distinguish observed instability from a runner problem.

Mutation contracts use supported deep snapshots of arguments before/after; unknown custom serializers or object identity produce UNKNOWN. A property requiring immutability must be approved or declared before a mutation is blocking.
