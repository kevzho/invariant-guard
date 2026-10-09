from invariantguard.models.enums import PropertyResult as P, RunOutcome, ScopeValidity as S, Severity, Verdict
from invariantguard.policy.verdict import PolicyInput, evaluate, severity_for


def test_severity_mapping():
    assert severity_for(P.PROVED_SAFE, S.EXACT_MODEL) is None
    assert severity_for(P.PROVED_UNSAFE, S.EXACT_MODEL) is Severity.ERROR
    assert severity_for(P.PROVED_UNSAFE, S.CONDITIONAL) is Severity.WARNING  # annotation-only stays advisory
    assert severity_for(P.POSSIBLY_UNSAFE, S.OVERAPPROXIMATE) is Severity.WARNING
    assert severity_for(P.UNKNOWN, None) is Severity.INFO


def test_precedence_error_block_incomplete_warn_pass():
    d = evaluate(PolicyInput([Severity.ERROR], unknown_required=3, skipped_required=0, engine_errors=1))
    assert (d.verdict, d.run_outcome, d.exit_code) == (Verdict.ERROR, RunOutcome.ERROR, 3)
    d = evaluate(PolicyInput([Severity.ERROR, Severity.WARNING], unknown_required=2, skipped_required=0, engine_errors=0))
    assert (d.verdict, d.run_outcome, d.exit_code) == (Verdict.BLOCK, RunOutcome.INCOMPLETE, 1)  # BLOCK and incomplete
    assert any("UNKNOWN" in r for r in d.reasons) and any("blocking" in r for r in d.reasons)
    d = evaluate(PolicyInput([Severity.WARNING], unknown_required=1, skipped_required=0, engine_errors=0))
    assert (d.verdict, d.exit_code) == (Verdict.INCOMPLETE, 2)
    d = evaluate(PolicyInput([], unknown_required=0, skipped_required=1, engine_errors=0))
    assert (d.verdict, d.run_outcome, d.exit_code) == (Verdict.INCOMPLETE, RunOutcome.INCOMPLETE, 2)
    d = evaluate(PolicyInput([Severity.WARNING], unknown_required=0, skipped_required=0, engine_errors=0))
    assert (d.verdict, d.run_outcome, d.exit_code) == (Verdict.WARN, RunOutcome.COMPLETE, 0)
    d = evaluate(PolicyInput([], unknown_required=0, skipped_required=0, engine_errors=0, capability_warnings=["solver: z3 unavailable"]))
    assert d.verdict is Verdict.WARN
    d = evaluate(PolicyInput([], 0, 0, 0))
    assert (d.verdict, d.run_outcome, d.exit_code) == (Verdict.PASS, RunOutcome.COMPLETE, 0)


def test_unknown_never_passes():
    for n in (1, 5):
        d = evaluate(PolicyInput([Severity.INFO] * n, unknown_required=n, skipped_required=0, engine_errors=0))
        assert d.verdict is not Verdict.PASS
