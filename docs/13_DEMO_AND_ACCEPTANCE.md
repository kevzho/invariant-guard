# 13. Demo and acceptance scenarios

## Demo promise

Show a real patch introducing one modeled runtime-failure path and one approved behavioral regression. Return concrete inputs to an agent; apply a real repair; verify the final captured snapshot. All numbers in the demo report must come from implementation, not this document.

## Fixture A: ratio

Baseline:

```python
def ratio(a: int, b: int):
    d = a - b
    if d == 0:
        return 0.0
    return 100 / d
```

Candidate:

```python
def ratio(a: int, b: int):
    d = a - b
    return 100 / d
```

Domain: exact Python built-in integer arguments, explicitly declared as admitted by the demo API configuration. Annotations alone would provide only conditional evidence.

Expected witness: `a=1, b=1`. Baseline returns 0.0; candidate has a zero denominator. Static output proves an existential failure in the declared modeled function-input domain. If execution is enabled, replay additionally observes ZeroDivisionError. This does not require claiming a production caller reaches the function.

Safe repair: restore an explicit zero guard. Its division obligation is safe under the declared domain. Other runtime errors remain outside that property.

## Fixture B: normalization

Baseline:

```python
def normalize(xs):
    total = sum(xs)
    return [x / total for x in xs]
```

Candidate:

```python
def normalize(xs):
    total = sum(xs) + 1.0
    return [x / total for x in xs]
```

Approved contract: a nonempty finite built-in numeric list with elements in [0,100] and positive total should yield finite results summing approximately to 1, with atol=rtol=1e-9.

Witness `xs=[1.0,1.0]`: baseline returns [0.5,0.5], summing to 1. Candidate returns approximately [1/3,1/3], summing to 2/3. Replay both in isolated environments. This is OBSERVED_REGRESSION, not static proof over arbitrary lists or floats.

All-zero input is outside this contract. Both versions can behave differently there; it must not be used to claim this positive-sum contract regressed.

Safe repair: restore the original total calculation, or implement a deliberate new API and revise the approved contract explicitly. Passing generated probes afterward remains TESTED evidence.

## Fixture C: honest unknown

```python
def imported_ratio(a, b):
    d = external_transform(a, b)
    return 100 / d
```

Unknown call output/effects make the division obligation UNKNOWN in the first supported subset. No fabricated witness or unconditional PASS. Required coverage leads to INCOMPLETE.

## Fixture D: changed callee

Baseline callee `denominator(x)` returns `x + 1` in an admitted domain `x >= 0`; caller divides by it. Candidate callee returns `x`. Caller source is unchanged. Input x=0 makes only the candidate divisor zero. The graph must invalidate the caller.

## Fixture E: mutation

Baseline `sorted_copy(xs)` returns `sorted(xs)`. Candidate calls `xs.sort()` and returns xs. With an approved unchanged-argument contract and input [2,1], baseline preserves the argument while candidate mutates it. Check using supported pre/post serialized snapshots. Merely naming the function sorted_copy is not sufficient authority.

## Demonstration sequence

After M5 is implemented, use a disposable example repository with no sensitive source:
1. Create a baseline commit with safe ratio and normalize.
2. Initialize configuration; declare the ratio domain and approve the normalization contract.
3. Configure an isolated Python run profile. Prepare dependencies explicitly.
4. Save baseline identity; apply the candidate changes.
5. Call `guard verify --base BASE_REF --format json --execute --run-profile isolated-python`.
6. Confirm introduced static evidence and true baseline/candidate regression.
7. Feed the report to the coding agent through CLI or MCP.
8. Agent restores the guard and normalization behavior.
9. Verify the repaired snapshot; show resolved findings, tested contracts and any remaining unknowns.
10. Show a separate no-model static run and actual local-model candidate discovery.

BASE_REF is a real captured reference selected during the demo, not a literal Git name to assume exists.

## Acceptance matrix

| Scenario | Required behavior |
| --- | --- |
| Unguarded ratio, declared domain | Exact modeled witness; admitted-domain block |
| Annotation only, unconfirmed callers | Conditional finding and advisory policy |
| Restored guard | Safe zero-denominator obligation within scope |
| Positive-sum normalization patch | Same input baseline pass/candidate fail |
| All-zero normalization input | Excluded domain, not this contract's regression |
| Inferred unapproved contract | Advisory change, no automatic intent authority |
| Both versions violate property | Pre-existing or invalid spec, no introduced-regression label |
| Unknown external call | Unknown obligation and incomplete required coverage |
| Changed callee | Unchanged caller is rechecked |
| Required solver timeout | Incomplete, never safe |
| Model unavailable | Deterministic mode still works |
| Dynamic backend unavailable | No unisolated fallback |
| Baseline dependencies missing | Comparison unknown; static work can still finish |
| Mid-run edit | Restart consistent capture or incomplete outcome |
| Deliberate contract revision | New audited revision; old evidence retained |

## Demo integrity

Keep baseline, patches, repairs, environment manifests, contract revisions and actual generated reports. Do not hand-edit findings or timings for the presentation. A concise terminal recording plus the JSON evidence is enough; a dashboard is unnecessary.

The demo establishes the implemented subset and repair workflow. It does not establish universal runtime-error prevention, arbitrary language support or sub-second performance.
