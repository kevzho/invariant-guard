"""benchmarks/agent_eval: the shipped packets are exactly what the tools produce, and score.py runs the shared pipeline.

The batch written here is a plumbing fixture for the scorer (one trivially true-by-construction claim);
it is not an evaluation proposal and no results file is written.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ..conftest import REPO

AE = REPO / "benchmarks" / "agent_eval"

# The shipped packets were generated under CPython 3.13. Snapshot identity includes the
# semantics profile (python<major.minor>-cpython-...), so they are reproducible only there.
pytestmark = pytest.mark.skipif(sys.version_info[:2] != (3, 13), reason="shipped agent_eval packets were generated under CPython 3.13 (snapshot identity includes the semantics profile)")


def _import():
    for p in (str(AE), str(REPO / "benchmarks")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import common
    import score

    return common, score


def test_shipped_packets_match_the_tools_for_every_heldout_function(tmp_path):
    common, _ = _import()
    from invariantguard.contracts.commands import cmd_context
    from invariantguard.models.schema import validation_errors
    from invariantguard.orchestration import CheckOptions, resolve_inputs

    m = common.manifest()
    root = common.make_workspace(tmp_path)
    r, cfg = resolve_inputs(CheckOptions(path=root, root=root, config_path=root / ".guard" / "config.toml"))
    index = json.loads((AE / "index.json").read_text())
    assert [f["symbol"] for f in index["functions"]] == [f["symbol"] for f in m["functions"]]
    for f in m["functions"]:
        shipped = json.loads((AE / "packets" / f"{common.slug(f['symbol'])}.json").read_text())
        assert shipped == cmd_context(r, cfg, f["symbol"]), f["symbol"]
        assert validation_errors("context_packet", shipped["context_packet"]) == []
    assert (AE / "README.md").is_file()
    assert (AE / "agent_candidate_batch.schema.json").read_bytes() == (REPO / "src" / "invariantguard" / "schemas" / "agent_candidate_batch.schema.json").read_bytes()


def test_score_runs_batches_through_the_shared_pipeline(tmp_path):
    common, score = _import()
    pkt = json.loads((AE / "packets" / "heldout_stats_py_mean_of.json").read_text())
    good = {"precondition": {"kind": "gt", "left": {"kind": "len", "value": {"kind": "arg", "name": pkt["parameters"][0]}},
                             "right": {"kind": "literal", "value": 0}},
            "predicate": {"kind": "unchanged_argument", "name": pkt["parameters"][0]}, "input_profile": pkt["input_profile"]["name"],
            "source_ref": {"snippet": 0, "start_line": pkt["context_packet"]["snippets"][0]["span"]["start_line"],
                           "end_line": pkt["context_packet"]["snippets"][0]["span"]["start_line"]},
            "rationale": "fixture", "uncertainty": "fixture", "unsupported_dependencies": [], "boundary_inputs": []}
    d = tmp_path / "proposals"
    d.mkdir()
    from invariantguard.models.schema import validation_errors

    fixture = {"schema_version": "1.0", "symbol": pkt["symbol"], "context_packet_digest": pkt["context_packet_digest"], "candidates": [good]}
    assert validation_errors("agent_candidate_batch", fixture) == []
    assert validation_errors("agent_candidate_batch", dict(fixture, authority="APPROVED")) != []
    (d / "a.json").write_text(json.dumps({"schema_version": "1.0", "symbol": pkt["symbol"], "context_packet_digest": pkt["context_packet_digest"],
                                          "candidates": [good, dict(good, authority="APPROVED")]}))
    (d / "b.json").write_text(json.dumps({"schema_version": "1.0", "symbol": "heldout/stats.py::spread", "context_packet_digest": "sha256:" + "1" * 64,
                                          "candidates": [good]}))
    (d / "c.json").write_text("not json")
    res = score.score_dir(d, client={"name": "fixture", "version": "0"}, execute=False, tmp=tmp_path)
    s = res["agent_proposed"]
    assert res["run_outcome"] == "INCOMPLETE" and any("--no-execute" in r for r in res["incomplete_reasons"])
    assert s["raw_candidates"] == 2 and s["rejection_codes"] == {"FORBIDDEN_FIELD": 1} and s["responses_rejected_whole"] == 1
    assert [b["rejection_code"] for b in s["batches"]] == [None, "STALE_CONTEXT"]
    assert {f["file"]: f["status"] for f in res["proposals"]["files"]} == {"a.json": "LOADED", "b.json": "LOADED", "c.json": "UNREADABLE"}
    assert len(res["proposals"]["symbols_not_proposed"]) == 11 and s["template_candidates_same_run"] > 0
    assert "new_beyond_templates" in s and "seeded_bug_functions_exposed" in s and "false_claims_refuted" in s
