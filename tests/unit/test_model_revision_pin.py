"""MODEL_REVISION must change whenever the modeled semantics change (I-139, I-145).

Validity keys hold AST fingerprints, not IR digests, so a lowering or analysis change that
keeps the AST but changes what it means would let a cache reuse stale results. This test
lowers and analyzes a fixed corpus (benchmarks/fixtures, examples and the differential
tests' inline corpora, deterministic solver limits) and compares a digest of the IR and of every obligation result with
``tests/unit/model_revision_pin.json``:

* digest changed, revision unchanged: fail. Bump ``impact.keys.MODEL_REVISION`` (and list
  the reason in its comment), then regenerate the pin;
* revision changed, pin not regenerated: fail until it is.

Regenerate with ``.venv/bin/python -m tests.unit.test_model_revision_pin``. A pure refactor
that changes the digest without changing meaning still needs the pin regenerated, so the
diff of the pin file shows up in review either way.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from invariantguard.analysis.obligations import Budget, site_obligation_id
from invariantguard.config import parse_config
from invariantguard.impact import keys
from invariantguard.models.spans import digest_json
from invariantguard.orchestration import analyze_lowered, lower_snapshot, setup_solver

REPO = Path(__file__).resolve().parents[2]
PIN = Path(__file__).with_name("model_revision_pin.json")
CORPUS = [REPO / "benchmarks" / "fixtures", REPO / "examples"]
CONFIG = b"""schema_version = "1.0"
[analysis]
quick_budget_ms = 600000
per_query_timeout_ms = 60000
per_query_rlimit = 10000000
"""


# Inline corpora of the differential tests (each construct the subset models).
INLINE = [("annotations", "test_annotations", "CORPUS"), ("generators", "test_generators", "CORPUS"), ("loops", "test_loops", "CORPUS"),
          ("loops_adversarial", "test_loops", "ADVERSARIAL"), ("try", "test_try", "CORPUS"),
          ("methods_formatting", "test_methods_and_formatting", "ADVERSARIAL"), ("m2_helpers", "test_m2_differential", "HELPERS")]


def corpus_files() -> list[tuple[str, bytes]]:
    import importlib

    out = [(f"pin/{name}.py", getattr(importlib.import_module(f"tests.differential.{mod}"), attr).encode()) for name, mod, attr in INLINE]
    for root in CORPUS:
        for p in sorted(root.rglob("*.py")):
            if "__pycache__" not in p.parts and ".guard" not in p.parts:
                out.append((p.relative_to(REPO).as_posix(), p.read_bytes()))
    return out


def model_digests() -> dict[str, str]:
    import time

    cfg = parse_config(CONFIG)
    solver = setup_solver(cfg, False)
    assert solver.state == "configured", solver.detail
    ir: dict[str, object] = {}
    results: dict[str, object] = {}
    for path, raw in corpus_files():
        # One snapshot per file: fixtures reuse module names (base/ and patch/ trees).
        snap = SimpleNamespace(files=[SimpleNamespace(path=path, raw=raw)], snapshot_id="sha256:" + "0" * 64, dependency_locks=[], uncaptured=[])
        lowered = lower_snapshot(snap, cfg)  # type: ignore[arg-type]
        ir[path] = {"modules": [m.to_json() for m in lowered.modules], "skipped": lowered.skipped, "errors": lowered.errors,
                    "unsupported": [(u.symbol_id, u.reason) for m in lowered.modules for u in m.unsupported_symbols]}
        budget = Budget(deadline=time.monotonic() + 600, per_query_timeout_ms=cfg.analysis.per_query_timeout_ms, witness_search=cfg.analysis.witness_search,
                        assume_no_str_rmod_override=cfg.analysis.assume_no_str_rmod_override,
                    assume_dividend_operator_respects_nonzero=cfg.analysis.assume_dividend_operator_respects_nonzero)
        analysis = analyze_lowered(lowered, cfg, solver.backend(cfg), solver.status, budget)
        assert not analysis.errors, (path, analysis.errors)
        for o in analysis.outcomes:
            results[site_obligation_id(o.symbol_id, o.site)] = [
                o.result.value, o.scope.value if o.scope else None, o.method.value, sorted(o.assumptions), o.witness, o.witness_validated,
                o.quantifier, o.runtime_mode, o.domain]
    return {"ir": digest_json(ir), "results": digest_json(results), "obligations": str(len(results))}


def test_model_revision_matches_pinned_semantics():
    pin = json.loads(PIN.read_text())
    got = model_digests()
    if keys.MODEL_REVISION != pin["model_revision"]:
        raise AssertionError(f"MODEL_REVISION is {keys.MODEL_REVISION} but the pin records {pin['model_revision']}: "
                             "regenerate it with `python -m tests.unit.test_model_revision_pin`")
    changed = [k for k in ("ir", "results") if got[k] != pin[k]]
    assert not changed, (f"the {' and '.join(changed)} digest of the pinned corpus changed under MODEL_REVISION {keys.MODEL_REVISION}: "
                         "bump impact.keys.MODEL_REVISION if the meaning of any lowering or result changed, then regenerate the pin "
                         "(`python -m tests.unit.test_model_revision_pin`)")


if __name__ == "__main__":
    data = {"model_revision": keys.MODEL_REVISION, **model_digests()}
    PIN.write_text(json.dumps(data, indent=2) + "\n")
    sys.stdout.write(json.dumps(data) + "\n")
