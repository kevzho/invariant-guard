"""``guard behavior``: maintainer decisions about replayed behavior changes (pivot phase 5).

    guard behavior accept FINDING_ID --reason TEXT [--run RUN_ID]
    guard behavior pin FINDING_ID --side base|patch --reason TEXT [--run RUN_ID]
    guard behavior retire DECISION_ID --reason TEXT
    guard behavior list [--format json|human]

Decisions read the recorded verify report (``.guard`` index) and append to the state
store; they never run code and never touch the repository. They are CLI-only on purpose:
the MCP server exposes no decision tool (an agent cannot approve its own change).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ..orchestration import CheckOptions, UsageError, now_utc, resolve_inputs
from ..storage.store import Store, StoreError
from . import decisions


def _state(args: Any) -> tuple[Path, Path]:
    root, _ = resolve_inputs(CheckOptions(path=args.path, root=args.root, config_path=None))
    sd = args.state_dir.resolve() if args.state_dir is not None else root / ".guard"
    return root, sd


def run(args: Any) -> int:
    from ..models.enums import EXIT_ERROR, EXIT_PASS, EXIT_USAGE
    from ..service import ServiceError, lookup_finding

    try:
        root, sd = _state(args)
        store = Store(sd)
    except (UsageError, StoreError) as exc:
        sys.stderr.write(f"guard: {exc}\n")
        return EXIT_USAGE
    try:
        if args.behavior_command == "list":
            ds = decisions.load(store)
            active = {d.decision_id for d in ds.active}
            rows = [{**d.to_json(), "active": d.decision_id in active} for d in ds.rows]
            if args.format == "json":
                sys.stdout.write(json.dumps({"schema_version": "1.0", "decisions": rows}, indent=2) + "\n")
            else:
                for r in rows:
                    rec = r["record"]
                    what = rec.get("witness", {}).get("call") or rec.get("call") or rec.get("retired", "")
                    sys.stdout.write(f"{r['decision_id']}  {r['kind']:<6} {'active ' if r['active'] else 'retired'} {r['symbol_id']}  {what}\n"
                                     f"    {r['created_at']}  reason: {r['reason']}\n")
                if not rows:
                    sys.stdout.write("no behavior decisions recorded\n")
            return EXIT_PASS
        created = now_utc()
        if args.behavior_command == "retire":
            d = decisions.retire(store, args.decision_id, args.reason, created)
        else:
            found = lookup_finding(root, args.finding_id, args.run, state_dir=sd if args.state_dir is not None else None)
            if args.behavior_command == "accept":
                d = decisions.accept(store, found.finding, args.reason, created, run_id=found.report["run_id"])
            else:
                d = decisions.pin(store, found.finding, found.report.get("evidence", []), args.side, args.reason, created, run_id=found.report["run_id"])
        sys.stdout.write(json.dumps({"schema_version": "1.0", "decision": d.to_json()}, indent=2) + "\n")
        return EXIT_PASS
    except decisions.DecisionError as exc:
        sys.stderr.write(f"guard: {exc}\n")
        return EXIT_USAGE
    except ServiceError as exc:
        sys.stderr.write(f"guard: {exc.status}: {exc.message}\n")
        return EXIT_USAGE if exc.status in ("INVALID_REQUEST", "NOT_FOUND") else EXIT_ERROR
    except StoreError as exc:
        sys.stderr.write(f"guard: {exc}\n")
        return EXIT_ERROR
    finally:
        store.close()


def add_parser(sub: Any, parser_class: Any) -> None:
    bh = sub.add_parser("behavior", help="maintainer decisions on replayed behavior changes: accept, pin, retire, list (never runs code)")
    bsub = bh.add_subparsers(dest="behavior_command", metavar="ACTION", parser_class=parser_class)

    def common(p: Any) -> None:
        p.add_argument("--path", type=Path, default=Path("."), help="a path inside the repository (default: .)")
        p.add_argument("--root", type=Path, default=None)
        p.add_argument("--state-dir", type=Path, default=None)

    a = bsub.add_parser("accept", help="record that a behavior difference (OBSERVED_DIFFERENCE finding) was intended; later identical differences are INFO")
    a.add_argument("finding_id")
    a.add_argument("--reason", required=True)
    a.add_argument("--run", default=None, help="run_id of the report (default: newest containing the finding)")
    common(a)
    pn = bsub.add_parser("pin", help="pin the base or patch outcome of a finding's literal witness as APPROVED behavior (a later change blocks)")
    pn.add_argument("finding_id")
    pn.add_argument("--side", required=True, choices=("base", "patch"))
    pn.add_argument("--reason", required=True)
    pn.add_argument("--run", default=None)
    common(pn)
    rt = bsub.add_parser("retire", help="retire an accept or pin decision (append-only; the decision stays in the log)")
    rt.add_argument("decision_id")
    rt.add_argument("--reason", required=True)
    common(rt)
    ls = bsub.add_parser("list", help="all decisions, active and retired")
    ls.add_argument("--format", choices=("json", "human"), default="human")
    common(ls)
