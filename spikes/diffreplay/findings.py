"""Proposed agent-facing output: cluster differences and map them onto the docs/03 axes.

usage: python findings.py REPO COMMIT_PREFIX  -> prints the JSON findings for that replayed commit

Mapping (proposal, see REPORT.md section 6):
* property: ``behavior.base_equivalence`` -- implicit, never written by a maintainer, so
  authority INFERRED unless the input is covered by a declared/approved source;
* property_result: OBSERVED_DIFFERENCE (proposed new label; NOT OBSERVED_REGRESSION, which
  needs a DECLARED/APPROVED property that base satisfies and patch violates);
* scope_validity EXECUTED; patch_relation CHANGED;
* severity: WARNING for confirmed observable differences on inputs the patch did not itself
  add; INFO for exception-message-only differences and for differences on inputs that come
  from tests/doctests added or edited by the same patch ("acknowledged by the patch");
* verdict contribution: WARN at most. Never BLOCK without an approved/declared contract.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from replay import DIFF_CLASSES

RES = Path(__file__).parent / "results"


def _shape(outcome: str) -> str:
    """Coarse outcome shape for clustering: returned / raised <Type>."""
    m = re.match(r"raised ([\w.]+)", outcome)
    if m:
        return "raised " + m.group(1)
    m = re.search(r"then raised ([\w.]+)", outcome)
    if m:
        return "lazily raised " + m.group(1)
    return "returned"


def findings_for(rec: dict) -> list[dict]:
    out = []
    for f in rec.get("functions", []):
        clusters: dict[tuple, dict] = {}
        for cls, exs in f["examples"].items():
            if cls not in DIFF_CLASSES:
                continue
            for e in exs:
                key = (cls, _shape(e["base"]), _shape(e["patch"]))
                c = clusters.setdefault(key, {"examples": [], "origins": set()})
                c["examples"].append(e)
                c["origins"].add(e["origin"])
        for (cls, bshape, pshape), c in clusters.items():
            ex = min(c["examples"], key=lambda e: len(e["input"]))
            acknowledged = all(o.endswith("_new") for o in c["origins"])
            severity = "INFO" if cls == "exception_message" or acknowledged else "WARNING"
            fid = "BD-" + hashlib.sha256(json.dumps([rec["commit"], f["target"], cls, bshape, pshape]).encode()).hexdigest()[:12]
            out.append({
                "finding_id": fid,
                "kind": "behavior_change",
                "changed_symbol": f["symbol"],
                "observed_through": {"target": f["target"], "relation": f["relation"]},
                "property_id": "behavior.base_equivalence",
                "property_result": "OBSERVED_DIFFERENCE",
                "scope_validity": "EXECUTED",
                "authority": "INFERRED",
                "patch_relation": "CHANGED",
                "severity": severity,
                "difference_class": cls,
                "witness": {"call": ex["input"], "input_origin": ex["origin"], "base": ex["base"], "patch": ex["patch"]},
                "cluster": {"examples_in_cluster": len(c["examples"]), "outcome_shape": {"base": bshape, "patch": pshape},
                            "input_origins": sorted(c["origins"])},
                "confirmation": "reproduced in fresh containers: first pass base x2 + patch x1, confirmation base x1 + patch x2 (string-hash randomization is active: the runner passes -I, which ignores PYTHONHASHSEED)",
                "question": (f"Your patch changed the behavior of {f['symbol'].split('::')[1]} on {ex['input'][:160]}: base {ex['base'][:160]}; "
                             f"patch {ex['patch'][:160]}. Was this intended?"),
                "if_intended": "say so in the patch rationale and record it: `guard behavior accept "
                               + fid + " --reason TEXT` (adds an auditable revision; the same witness then reports INFO)",
                "if_not_intended": "fix the patch; re-run verify_patch to replay the same witness",
                "acknowledged_by_patch_tests": acknowledged,
            })
    return out


def main() -> None:
    repo, prefix = sys.argv[1], sys.argv[2]
    for line in (RES / f"replay_{repo}.jsonl").read_text().splitlines():
        rec = json.loads(line)
        if rec["commit"].startswith(prefix):
            print(json.dumps(findings_for(rec), indent=1))
            return
    raise SystemExit("commit not found")


if __name__ == "__main__":
    main()
