"""Behavioral non-vacuity of discovered candidates (M4 T43; shared by templates and the model).

``validate_revision`` already rejects predicates that never mention behavior (VACUOUS)
and preconditions with no admissible input (INVALID_CONTRACT). A predicate can still be a
tautology over behavior, e.g. ``le(0, len(result))`` for list results or
``or(eq(result, result), ...)``: it mentions ``result`` but no observable behavior can
violate it, so checking it can never find anything.

The check below is pure (nothing is executed): for up to four admitted inputs it
evaluates the canonical predicate with the exact DSL evaluator against a fixed pool of
*synthetic* behaviors (results of several shapes and magnitudes, derived variants of the
inputs, mutated and unmutated argument snapshots, identical and differing repeated
results). The candidate is non-vacuous iff at least one synthetic behavior makes the
predicate FALSE for a reason other than a type mismatch (a predicate that only a
wrongly-typed result can violate says nothing about the function's values).

This is a bounded search, so it is a filter, not a proof of anything: a rejected candidate
might be falsifiable by a behavior outside the pool (it is then reported as rejected with
the reason), and an accepted one is merely *able* to fail.
"""

from __future__ import annotations

import math
from typing import Any

from ..models.spans import canonical_json
from .dsl import EvalEnv, Rejection, evaluate
from .model import TypedContract
from .profiles import admit_inputs, domain_hints
from .values import encode

MAX_ARGSETS = 4


def _enc(v: Any) -> str:
    return canonical_json(encode(v)).decode("utf-8")


def _variants(v: Any) -> list[Any]:
    if type(v) is list:
        out = [list(v), sorted(v), list(reversed(v)), [x * 2 for x in v], [-x for x in v], v + [0], v[:-1], [x + 1 for x in v], [abs(x) for x in v]]
        if v:
            out.append([max(v)] * len(v))
        return out
    if type(v) is bool:
        return [v, not v]
    if type(v) in (int, float):
        return [v, -v, v + 1, v - 1, v * 2, 0]
    if type(v) is str:
        return [v, "", v + "a", v[:-1], v[::-1], v.upper(), v.lower(), v.strip(), v * 2]
    return [v]


_POOL: list[Any] = [None, True, False, 0, 1, -1, 0.5, -0.5, 7, -1e9, 1e9, math.inf, -math.inf, [], [0], [1], [-1], [0.5, 0.5], [1e9],
                    [-1e9, 1e9], list(range(40)), [0.0, 0.0, 0.0]]


def behaviors(args: dict[str, Any]) -> list[Any]:
    out = list(_POOL)
    for v in args.values():
        out.extend(_variants(v))
    seen, uniq = set(), []
    for r in out:
        k = _enc(r)
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def falsifiable(tc: TypedContract, args: dict[str, Any]) -> tuple[bool, str | None]:
    lists = [p for p, v in args.items() if type(v) is list]
    before_enc = {p: _enc(v) for p, v in args.items()}
    after_options: list[dict[str, Any]] = [dict(args)]
    for p in lists:
        changed = dict(args)
        changed[p] = args[p] + [0]
        after_options.append(changed)
        if args[p]:
            changed2 = dict(args)
            changed2[p] = [args[p][0] + 1] + args[p][1:]
            after_options.append(changed2)
    reps = tc.repeat["repeats"] if tc.repeat else 1
    pool = behaviors(args)
    for r in pool:
        rep_options = [[_enc(r)] * reps]
        if reps > 1:
            rep_options.append([_enc(r)] * (reps - 1) + [_enc([r, 1])])
        for after in after_options:
            for encs in rep_options:
                env = EvalEnv(args=args, result=r, has_result=True, before=dict(args), after=after, before_enc=before_enc,
                              after_enc={p: _enc(v) for p, v in after.items()}, repeat_encodings=encs)
                res = evaluate(tc.predicate, env)
                if res.status == "FALSE" and not res.reason.startswith("type mismatch"):
                    return True, f"falsified by synthetic behavior result={r!r}" + (" with a mutated argument" if after is not after_options[0] else "")
    return False, None


def check_nonvacuous(tc: TypedContract) -> str:
    """Raise ``Rejection('VACUOUS_PREDICATE')`` unless some synthetic behavior violates the predicate."""
    params = list(tc.target.params)
    seed = int(tc.semantic_digest.split(":", 1)[1][:15], 16)
    adm = admit_inputs(tc.input_profile, params, tc.precondition_holds, domain_hints(tc.precondition, params), seed=seed, limit=MAX_ARGSETS,
                       examples=tc.examples, max_attempts=64)
    argsets = [a for a, _ in adm.inputs] or ([tc.admissible_witness] if tc.admissible_witness else [])
    for args in argsets:
        ok, why = falsifiable(tc, args)
        if ok:
            return why or "falsifiable"
    raise Rejection("VACUOUS_PREDICATE", "predicate",
                    f"no synthetic behavior (of {len(behaviors(argsets[0])) if argsets else 0} results x argument/repeat variants, on {len(argsets)} "
                    "admitted input(s)) violates the predicate except by a type mismatch: it cannot detect a behavior change (tautology over the "
                    "bounded behavior pool)")


__all__ = ["check_nonvacuous", "falsifiable", "behaviors"]
