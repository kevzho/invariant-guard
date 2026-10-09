"""InvariantGuard: local-first verification layer for coding-agent patches.

This package implements milestones M0 (foundation), M1 (single-snapshot static Python
slice), M2 (change-aware verification: snapshots, baselines, impact slices, call
summaries, caches, bounds and None-flow checks, patch attribution), M3 (behavioral
contracts, isolated runner), M4 (local open-weight contract proposals) and M5 (stdio MCP
server over the shared orchestration, cancellation, evidence retrieval), plus opt-in
bring-your-own-key and agent contract proposers (DECISIONS I-105/I-106). See
docs/IMPLEMENTATION_STATUS.md for the exact supported subset.
"""

__version__ = "0.5.0"
ENGINE_NAME = "invariantguard"
ENGINE_VERSION = __version__
REPORT_SCHEMA_VERSION = "1.0"
