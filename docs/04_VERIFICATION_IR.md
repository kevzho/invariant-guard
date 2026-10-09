# 04. Verification IR and language adapters

## Goal

Reuse analysis structure across languages without erasing semantic differences. The IR is a compact, typed, source-mapped representation of verification obligations and effects. It is not a universal claim that identically spelled operators behave identically.

## Minimal v1 objects

| Object | Required fields |
| --- | --- |
| Module | schema_version, language, semantics_profile, source_digest, symbols, imports, coverage |
| Function | symbol_id, parameters, declared_domain, blocks, entry_id, exits, effects, source_span |
| Block | block_id, instructions, terminator, predecessor IDs |
| Instruction | op, operands, result_id, semantic_type, source_span, effect |
| Expression | kind, typed operands, literal value or symbol reference |
| Type | language type, numeric semantics, nullability and certainty origin |
| SourceSpan | relative path, start/end line and column, byte offsets and snapshot ID |
| Unsupported | original span, reason, dependent values and effects |
| Summary | preconditions, result facts, possible exceptions, effects and validity keys |

Use immutable IDs within a snapshot. Semantic identity for matching symbols across snapshots is separate from source line numbers. Lower simple assignments to versioned value IDs and joins to phi-like merges when needed; do not force full SSA infrastructure before the first checks work.

## Initial instruction vocabulary

`CONST`, `PARAM`, `ASSIGN`, `ADD`, `SUB`, `MUL`, `DIV`, `FLOORDIV`, `MOD`, `COMPARE`, `IS_NONE`, `LEN`, `INDEX`, `CALL`, `ATTRIBUTE`, `RETURN`, `RAISE`, `BRANCH`, `JUMP`, `PHI`, `UNKNOWN_EFFECT`.

Each operation includes a semantics profile or explicit operation flavor. `DIV` cannot mean generic mathematical division for every frontend. For M1, it need only capture the zero-denominator condition for exact built-in integer operands; it must not claim accurate floating-point results.

## Semantic profiles

| Case | Required handling |
| --- | --- |
| Python built-in integer arithmetic | Unbounded integer addition/subtraction; numeric overload uncertainty tracked |
| Python `/`, `//`, `%` | Built-in zero divisor raises; floor/modulo rules differ from truncation |
| Python sequence index | Negative indices are valid within bounds; slicing has different behavior |
| Kotlin/JVM `Int` | Fixed-width arithmetic; overflow behavior modeled, not mathematical integers |
| Kotlin nullable values | Smart casts, `!!`, Java platform types and declared types have distinct precision |
| JavaScript Number division | Zero denominator can yield Infinity or NaN; do not emit generic division exception |
| JavaScript BigInt division | Distinct integer semantics and zero-divisor error behavior |
| TypeScript array access | Out-of-range access can yield undefined; subsequent use is the relevant failure |
| Rust/C/C++ | Adapter-specific panic, wrapping, undefined behavior and build-mode profiles required |

The Kotlin and JavaScript rows are architectural requirements for later frontends. They are not M1 implemented capabilities.

## Unsupported behavior

Dynamic dispatch, reflection, native calls, unknown imports, decorators, unsupported exception handling or mutation can invalidate relevant facts. Lower these to unknown results and effect summaries. Taint downstream obligations that depend on them. Report exactly why the property is unresolved; do not drop the function from the denominator.

An unknown call may mutate shared state, throw, call back into the repository or return an arbitrary value. A trusted library summary can narrow that effect, but its version and authority must be explicit.

## Frontend adapter protocol

An adapter advertises languages/versions, supported syntax, supported type domains, effect precision, library summaries and compiler requirements. It emits versioned IR plus diagnostics and coverage. The core rejects incompatible IR versions. Parsing success is not semantic-analysis coverage.

Python frontend initially uses AST without imports. Kotlin sidecar uses typed compiler information in an explicitly configured environment. Later frontends can use compiler APIs or parsers, but must pass the same shared adapter tests.

## Conformance tests

Every numeric rule requires safe and unsafe boundary cases, signed values, relevant overflow or precision cases, branches, overloaded/unknown variants and source-span tests. Compare exact modeled operations against isolated language executions in the test suite. Production static analysis still executes no repository code.

Add a fixture where Python `xs[-1]` is valid for a nonempty built-in list; a Kotlin negative array index is not. Add Number-versus-BigInt JavaScript cases before enabling that adapter.

## Example lowering

```python
def ratio(a: int, b: int):
    d = a - b
    if d != 0:
        return 100 / d
    return 0.0
```

```text
domain: a,b are exact Python built-in integers (declared assumption)
entry:
  v0 = PARAM a
  v1 = PARAM b
  v2 = SUB v0 v1 [python.bigint]
  v3 = COMPARE NE v2 0
  BRANCH v3 nonzero zero
nonzero:
  v4 = DIV 100 v2 [python.true_division]
  RETURN v4
zero:
  RETURN 0.0
```

The division obligation in the nonzero block is `v2 != 0 AND v2 == 0`, which is unsatisfiable. This proves the zero-denominator failure absent for that block under the domain assumptions; it does not prove freedom from every other possible error.
