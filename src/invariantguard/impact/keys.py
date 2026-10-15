"""Summary validity keys (docs/07 "Cache key").

A symbol's analysis result is a deterministic function of its key material:

* global material: engine/IR versions, host interpreter, semantics profile and model
  revision, analysis
  configuration that can change results (budgets, solver settings, excludes), solver
  identity, dependency-lock digests and the contract-set slot;
* the symbol ID, the position-free AST fingerprint of its definition, its declared
  domain(s);
* every dependency record the lowering produced: each global/builtin/annotation name it
  resolved (with a digest of *all* module bindings of that name and the module flags),
  each callee body it instantiated or examined (with that callee's own records,
  transitively), and cross-module import mappings.

No source line numbers enter the key, so a comment-only or layout-only edit reuses the
analysis (current spans are re-derived from fresh lowering). Any missing dependency
would make reuse unsound, which is why ``tests/integration/test_incremental.py``
compares incremental and full results on fixtures.
"""

from __future__ import annotations

import sys
from typing import Any

from .. import ENGINE_VERSION
from ..frontends.python import domain_decls
from ..ir.model import IR_SCHEMA_VERSION
from ..models.spans import digest_json

# M3: the global slot names the scheme; each symbol's key additionally carries the digest
# of the active contract revisions scoped to it (id, revision, semantic digest, authority),
# so declaring, approving, revising or retiring a contract invalidates exactly the
# summaries of its target symbol (T23 contract dependencies, T38).
# Revision of the modeled static semantics (lowering, evaluator, analyses). Bump it whenever a
# change alters what a symbol's lowering means without changing its AST (I-139): validity keys
# hold AST fingerprints, not IR digests. It is kept out of the semantics profile, which is part
# of snapshot identity (shipped benchmark packets pin it). Revisions: 1 = through I-133 (try);
# 2 = generator functions and generator calls (I-138/I-139); 3 = generator pre-start
# frame-introspection assumption (I-140); 4 = handler-marker order and the shared-list
# asynchronous-mutation assumption (I-141); 5 = str annotations and `str % x` (I-134..I-137).
MODEL_REVISION = 5

CONTRACT_SLOT = "per-symbol: digest of active contract revisions scoped to the symbol (contracts-v1)"


def global_key_material(cfg: Any, solver_identity: str, semantics_profile: str, locks: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "engine": ENGINE_VERSION,
        "ir": IR_SCHEMA_VERSION,
        "python": sys.version.split()[0],
        "semantics": semantics_profile,
        "model_revision": MODEL_REVISION,
        "analysis": cfg.analysis_key(),
        "solver": solver_identity,
        "locks": [{"path": d["path"], "sha256": d["sha256"]} for d in locks],
        "contracts": CONTRACT_SLOT,
    }


def unit_key(symbol_id: str, fingerprint: str, deps: list[dict[str, Any]], domains: dict[str, Any], global_digest: str,
             contracts: dict[str, str] | None = None) -> tuple[str, dict[str, Any]]:
    decls = sorted(
        ({"profile": d.profile, "authority": d.authority, "reason": d.reason, "params": list(d.params) if d.params is not None else None}
         for d in domain_decls(domains.get(symbol_id))),
        key=lambda x: (x["profile"], str(x["params"])),
    )
    material = {"global": global_digest, "symbol": symbol_id, "fingerprint": fingerprint, "domains": decls, "deps": deps,
                "contracts": (contracts or {}).get(symbol_id)}
    return digest_json(material), material


def snapshot_keys(lowered: Any, cfg: Any, solver_identity: str, semantics_profile: str,
                  contracts: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """(global digest, symbol_id -> key) for every lowered and unsupported symbol.

    ``contracts`` maps a symbol to the digest of its active contract revisions
    (``contracts.registry.contract_scope_digests``).
    """
    g = digest_json(global_key_material(cfg, solver_identity, semantics_profile, lowered.snap.dependency_locks))
    keys: dict[str, str] = {}
    for m in lowered.modules:
        for fn in m.functions:
            keys[fn.symbol_id] = unit_key(fn.symbol_id, fn.fingerprint, fn.deps, lowered.domains, g, contracts)[0]
        for u in m.unsupported_symbols:
            keys[u.symbol_id] = unit_key(u.symbol_id, u.fingerprint, [{"kind": "unsupported", "reason": u.reason}], lowered.domains, g, contracts)[0]
    return g, keys
