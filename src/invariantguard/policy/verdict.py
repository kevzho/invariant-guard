"""Default gate policy ``default-v1`` (docs/03) and CLI exit codes (docs/09).

Precedence: ERROR > BLOCK > INCOMPLETE > WARN > PASS. ``run_outcome`` is kept separate,
so a BLOCK report can also be INCOMPLETE.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models.enums import (
    PatchRelation,
    EXIT_BLOCK,
    EXIT_ERROR,
    EXIT_INCOMPLETE,
    EXIT_PASS,
    PropertyResult,
    RunOutcome,
    ScopeValidity,
    Severity,
    Verdict,
)

POLICY_ID = "default-v1"


def severity_for(result: PropertyResult, scope: ScopeValidity | None) -> Severity | None:
    """Severity of a single obligation result in a single-snapshot check.

    ``guard check`` has no patch attribution (relation UNDETERMINED). An exact modeled
    failure under a declared or literal-only domain is blocking; a conditional failure
    (annotation-only domain, or only under ``python -O``) is advisory; an overapproximate
    SAT is advisory and never a proof; UNKNOWN is informational and drives INCOMPLETE.
    """
    if result is PropertyResult.PROVED_SAFE:
        return None
    if result is PropertyResult.PROVED_UNSAFE:
        return Severity.ERROR if scope is ScopeValidity.EXACT_MODEL else Severity.WARNING
    if result is PropertyResult.POSSIBLY_UNSAFE:
        return Severity.WARNING
    return Severity.INFO


def severity_for_patch(result: PropertyResult, scope: ScopeValidity | None, relation: PatchRelation) -> Severity | None:
    """Severity in ``guard verify`` (docs/03 default gate policy rows).

    * INTRODUCED or CHANGED exact modeled failure (declared/literal domain): ERROR (block).
      CHANGED means a pre-existing defect whose violating domain grew: the new failing
      inputs are introduced by the patch.
    * Introduced conditional failures and overapproximate SATs: WARNING.
    * PRE_EXISTING failures: reported separately as WARNING, never a new block.
    * UNDETERMINED failures: WARNING; an exact one also makes the run INCOMPLETE
      (attribution is required work; ``verify.py``).
    * RESOLVED and UNKNOWN: INFO (UNKNOWN required obligations drive INCOMPLETE).
    """
    if result is PropertyResult.PROVED_SAFE:
        return None
    if result is PropertyResult.UNKNOWN or relation is PatchRelation.RESOLVED:
        return Severity.INFO
    if relation in (PatchRelation.INTRODUCED, PatchRelation.CHANGED):
        if result is PropertyResult.PROVED_UNSAFE and scope is ScopeValidity.EXACT_MODEL:
            return Severity.ERROR
        return Severity.WARNING
    return Severity.WARNING


@dataclass
class PolicyInput:
    severities: list[Severity]
    unknown_required: int
    skipped_required: int
    engine_errors: int
    capability_warnings: list[str] = field(default_factory=list)
    unknown_advisory: int = 0
    skipped_advisory: int = 0
    files_analyzed: int | None = None
    # M2 (verify): extra advisory explanations and required work left undone that is not
    # an obligation result (e.g. findings whose patch relation could not be determined).
    extra_reasons: list[str] = field(default_factory=list)
    incomplete_reasons: list[str] = field(default_factory=list)
    # M3: how many of the ERROR severities are replayed regressions of approved/declared contracts.
    contract_errors: int = 0
    # M3: advisory conditions that make an otherwise clean run WARN (e.g. a requested runner was
    # unavailable for advisory contracts, or an INFERRED contract check stayed undecided).
    warn_reasons: list[str] = field(default_factory=list)


@dataclass
class PolicyDecision:
    verdict: Verdict
    run_outcome: RunOutcome
    reasons: list[str]
    exit_code: int


def evaluate(inp: PolicyInput) -> PolicyDecision:
    reasons: list[str] = []
    errors = sum(1 for s in inp.severities if s is Severity.ERROR)
    warnings = sum(1 for s in inp.severities if s is Severity.WARNING)
    incomplete = inp.unknown_required > 0 or inp.skipped_required > 0 or bool(inp.incomplete_reasons)
    if inp.engine_errors:
        run_outcome = RunOutcome.ERROR
    elif incomplete:
        run_outcome = RunOutcome.INCOMPLETE
    else:
        run_outcome = RunOutcome.COMPLETE
    if inp.engine_errors:
        reasons.append(f"{inp.engine_errors} engine error(s): verifier failure, not a program defect")
    static_errors = errors - inp.contract_errors
    if static_errors:
        reasons.append(f"{static_errors} blocking finding(s): exact modeled failure in an admitted domain")
    if inp.contract_errors:
        reasons.append(f"{inp.contract_errors} blocking contract regression(s): replayed regression of an APPROVED or DECLARED contract")
    if inp.unknown_required:
        reasons.append(f"{inp.unknown_required} required obligation(s) UNKNOWN")
    if inp.skipped_required:
        reasons.append(f"{inp.skipped_required} required check(s) skipped (e.g. unparsable file or changed source)")
    reasons.extend(inp.incomplete_reasons)
    if warnings:
        reasons.append(f"{warnings} advisory finding(s) (conditional, overapproximate or pre-existing)")
    reasons.extend(inp.extra_reasons)
    if inp.unknown_advisory:
        reasons.append(f"{inp.unknown_advisory} non-required obligation(s) UNKNOWN (require_coverage = false)")
    if inp.skipped_advisory:
        reasons.append(f"{inp.skipped_advisory} non-required check(s) skipped (require_coverage = false)")
    for w in inp.capability_warnings:
        reasons.append(f"capability unavailable: {w}")
    reasons.extend(inp.warn_reasons)
    if inp.engine_errors:
        verdict, code = Verdict.ERROR, EXIT_ERROR
    elif errors:
        verdict, code = Verdict.BLOCK, EXIT_BLOCK
    elif incomplete:
        verdict, code = Verdict.INCOMPLETE, EXIT_INCOMPLETE
    elif warnings or inp.capability_warnings or inp.unknown_advisory or inp.skipped_advisory or inp.warn_reasons:
        verdict, code = Verdict.WARN, EXIT_PASS
    else:
        verdict, code = Verdict.PASS, EXIT_PASS
        if inp.files_analyzed == 0:
            reasons.append("no Python source files in scope: nothing was analyzed (vacuous PASS)")
        else:
            reasons.append("all required obligations resolved; no findings")
    return PolicyDecision(verdict, run_outcome, reasons, code)
