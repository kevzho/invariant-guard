"""Shared workspace for the agent-proposer evaluation (packets and scoring must see the identical snapshot).

The context-packet digest covers the snapshot id, which covers the configuration digest. Packets
(make_packets.py) and scoring (score.py) therefore build the workspace with exactly this function
and this configuration text; any other settings make every batch STALE_CONTEXT by design.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "benchmarks" / "fixtures" / "m4_heldout"
MANIFEST = REPO / "benchmarks" / "manifests" / "m4_heldout.json"
HERE = Path(__file__).resolve().parent
PACKETS = HERE / "packets"
IMAGE = "python:3.13-slim"
MAX_PROBES = 32

# Identical to the M4 evaluation's runner settings (benchmarks/model_eval.py), without a model section:
# the agent is the proposer, so no model endpoint is configured or contacted.
CONFIG = f'''schema_version = "1.0"
[execution]
enabled = true
profile = "iso"
[execution.profiles.iso]
backend = "docker"
image = "{IMAGE}"
allowed_targets = ["heldout/*.py::*"]
max_probes = {MAX_PROBES}
'''


def slug(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", symbol).strip("_")


def manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def make_workspace(parent: Path, name: str = "ws") -> Path:
    root = parent / name
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (root / ".git").mkdir()  # stop configuration discovery at this root (no Git repository is needed)
    (root / ".guard").mkdir()
    (root / ".guard" / "config.toml").write_text(CONFIG, encoding="utf-8")
    return root
