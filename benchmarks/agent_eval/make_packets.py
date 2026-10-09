"""Write the exact ContextPacket (plus candidate schema, DSL reference and protocol) for every M4 held-out function.

    .venv/bin/python benchmarks/agent_eval/make_packets.py

Each file is the output of ``guard contracts context SYMBOL --format json`` (the same function the
MCP tool ``get_context_packet`` calls) in a fresh workspace built by common.make_workspace. Nothing is
imported or executed from the fixture, and no model is contacted.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PACKETS, REPO, make_workspace, manifest, slug  # noqa: E402


def main() -> int:
    sys.path.insert(0, str(REPO / "src"))
    from invariantguard.contracts.commands import cmd_context
    from invariantguard.orchestration import CheckOptions, resolve_inputs

    PACKETS.mkdir(parents=True, exist_ok=True)
    m = manifest()
    with tempfile.TemporaryDirectory(prefix="ig-agent-packets-") as tmp:
        root = make_workspace(Path(tmp))
        r, cfg = resolve_inputs(CheckOptions(path=root, root=root, config_path=root / ".guard" / "config.toml"))
        index = []
        for f in m["functions"]:
            out = cmd_context(r, cfg, f["symbol"])
            path = PACKETS / f"{slug(f['symbol'])}.json"
            path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            index.append({"symbol": f["symbol"], "file": f"packets/{path.name}", "context_packet_digest": out["context_packet_digest"],
                          "snapshot_id": out["snapshot_id"], "input_profile": out["input_profile"]["name"]})
            print(f"{f['symbol']} -> {path.relative_to(REPO)}", file=sys.stderr)
    schema = REPO / "src" / "invariantguard" / "schemas" / "agent_candidate_batch.schema.json"
    (PACKETS.parent / schema.name).write_bytes(schema.read_bytes())  # the generic batch schema, kept identical to the shipped one
    (PACKETS.parent / "index.json").write_text(json.dumps({"schema_version": "1.0", "manifest": "benchmarks/manifests/m4_heldout.json",
                                                            "functions": index}, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
