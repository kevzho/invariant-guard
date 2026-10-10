"""Diff-to-symbol mapping, base/candidate dependency graphs and the affected slice (docs/07).

* ``diff_files``: added / deleted / modified / unchanged paths, plus exact renames
  (a deleted and an added path with identical bytes, unique on both sides).
* ``match_symbols``: candidate symbol -> base symbol by qualified name (through file
  renames), then certain renames (identical body with the name blanked). Other
  delete/add pairs with the same parameter list in the same module are *uncertain*
  renames: they are disclosed and their findings get relation UNDETERMINED.
* ``build_graph``: nodes are symbols and module globals; edges are calls (summary
  instantiations and examined callees), references (every global/builtin/annotation
  name a symbol resolved) and dynamic (unresolved call) edges.
* ``affected_slice``: changed/added/deleted symbols and changed module bindings seed a
  reverse walk over *both* graphs (removals matter as much as additions). A global
  configuration/lock/engine change affects everything. Finally every candidate symbol
  whose validity key differs from its base counterpart's must be in the slice; any that
  the graph walk missed is added and disclosed (fail closed).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..snapshots.capture import Snapshot


@dataclass
class FileChanges:
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    renamed: dict[str, str] = field(default_factory=dict)  # old path -> new path (identical bytes)

    def to_json(self) -> dict[str, Any]:
        return {"added": self.added, "deleted": self.deleted, "modified": self.modified,
                "renamed": [{"from": a, "to": b} for a, b in sorted(self.renamed.items())], "unchanged_count": len(self.unchanged)}


def diff_files(base: Snapshot, cand: Snapshot) -> FileChanges:
    b, c = base.file_map(), cand.file_map()
    ch = FileChanges()
    ch.added = sorted(set(c) - set(b))
    ch.deleted = sorted(set(b) - set(c))
    for p in sorted(set(b) & set(c)):
        (ch.unchanged if b[p].digest == c[p].digest else ch.modified).append(p)
    by_del: dict[str, list[str]] = {}
    by_add: dict[str, list[str]] = {}
    for p in ch.deleted:
        by_del.setdefault(b[p].digest, []).append(p)
    for p in ch.added:
        by_add.setdefault(c[p].digest, []).append(p)
    for digest, olds in by_del.items():
        news = by_add.get(digest, [])
        if len(olds) == 1 and len(news) == 1:
            ch.renamed[olds[0]] = news[0]
    ch.added = [p for p in ch.added if p not in set(ch.renamed.values())]
    ch.deleted = [p for p in ch.deleted if p not in ch.renamed]
    return ch


@dataclass
class Sym:
    symbol_id: str
    path: str
    qualname: str
    kind: str  # function | unsupported
    fingerprint: str
    body_fingerprint: str
    params: tuple[str, ...]
    key: str
    code_fingerprint: str = ""  # docstrings blanked; "" when unknown (stored baselines before v2)


def symbol_table(lowered: Any, keys: dict[str, str]) -> dict[str, Sym]:
    out: dict[str, Sym] = {}
    for m in lowered.modules:
        for fn in m.functions:
            path, q = fn.symbol_id.split("::", 1)
            out[fn.symbol_id] = Sym(fn.symbol_id, path, q, "function", fn.fingerprint, fn.body_fingerprint,
                                    tuple(p.name for p in fn.params), keys.get(fn.symbol_id, ""), fn.code_fingerprint)
        for u in m.unsupported_symbols:
            path, q = u.symbol_id.split("::", 1)
            out[u.symbol_id] = Sym(u.symbol_id, path, q, "unsupported", u.fingerprint, u.fingerprint, (), keys.get(u.symbol_id, ""), u.code_fingerprint)
    return out


@dataclass
class SymbolMatch:
    cand_to_base: dict[str, tuple[str, str]] = field(default_factory=dict)  # cand -> (base, how)
    added: list[str] = field(default_factory=list)  # candidate-only symbols
    deleted: list[str] = field(default_factory=list)  # base-only symbols
    modified: list[str] = field(default_factory=list)  # candidate ids whose definition changed
    doc_only: list[str] = field(default_factory=list)  # subset of modified: only docstrings differ
    unchanged: list[str] = field(default_factory=list)
    uncertain: dict[str, str] = field(default_factory=dict)  # cand -> base (possible rename; not matched)

    def base_to_cand(self) -> dict[str, str]:
        return {b: c for c, (b, _) in self.cand_to_base.items()}

    def to_json(self) -> dict[str, Any]:
        return {
            "added": self.added, "deleted": self.deleted, "modified": self.modified, "doc_only": self.doc_only, "unchanged_count": len(self.unchanged),
            "renamed": sorted([{"from": b, "to": c, "match": how} for c, (b, how) in self.cand_to_base.items() if how != "anchor"], key=lambda x: x["to"]),
            "uncertain_renames": [{"from": b, "to": c} for c, b in sorted(self.uncertain.items())],
        }


def match_symbols(base: dict[str, Sym], cand: dict[str, Sym], files: FileChanges) -> SymbolMatch:
    m = SymbolMatch()
    unmatched_base = set(base)
    for cid, cs in cand.items():
        if cid in base:
            m.cand_to_base[cid] = (cid, "anchor")
            unmatched_base.discard(cid)
    inv_rename = {new: old for old, new in files.renamed.items()}
    for cid, cs in cand.items():
        if cid in m.cand_to_base or cs.path not in inv_rename:
            continue
        bid = f"{inv_rename[cs.path]}::{cs.qualname}"
        if bid in unmatched_base:
            m.cand_to_base[cid] = (bid, "anchor_renamed_file")
            unmatched_base.discard(bid)
    # Certain renames: identical definition apart from the name, unique on both sides.
    cand_left = [c for c in cand if c not in m.cand_to_base]
    by_body_b: dict[tuple[str, str], list[str]] = {}
    for b in unmatched_base:
        if base[b].kind == "function":
            by_body_b.setdefault((base[b].path, base[b].body_fingerprint), []).append(b)
    by_body_c: dict[tuple[str, str], list[str]] = {}
    for c in cand_left:
        if cand[c].kind == "function":
            p = cand[c].path
            by_body_c.setdefault((inv_rename.get(p, p), cand[c].body_fingerprint), []).append(c)
    for k, cs_ in by_body_c.items():
        bs = by_body_b.get(k, [])
        if len(cs_) == 1 and len(bs) == 1 and "." not in cand[cs_[0]].qualname:
            m.cand_to_base[cs_[0]] = (bs[0], "anchor_renamed_symbol")
            unmatched_base.discard(bs[0])
    cand_left = [c for c in cand if c not in m.cand_to_base]
    # Uncertain renames: same module, same parameter list, not otherwise matched.
    for c in cand_left:
        cs = cand[c]
        if cs.kind != "function":
            continue
        cands = [b for b in unmatched_base if base[b].kind == "function" and base[b].path == inv_rename.get(cs.path, cs.path) and base[b].params == cs.params]
        if len(cands) == 1:
            m.uncertain[c] = cands[0]
    m.added = sorted(cand_left)
    m.deleted = sorted(unmatched_base)
    for c, (b, how) in sorted(m.cand_to_base.items()):
        same = cand[c].body_fingerprint == base[b].body_fingerprint if how == "anchor_renamed_symbol" else cand[c].fingerprint == base[b].fingerprint
        (m.unchanged if same else m.modified).append(c)
        if not same and how == "anchor" and cand[c].code_fingerprint and cand[c].code_fingerprint == base[b].code_fingerprint:
            m.doc_only.append(c)
    return m


@dataclass
class DepGraph:
    edges: list[tuple[str, str, str]] = field(default_factory=list)  # (symbol, kind, target)
    reverse: dict[str, set[str]] = field(default_factory=dict)
    dynamic: list[tuple[str, str]] = field(default_factory=list)  # (symbol, unresolved call)
    descriptors: dict[str, set[str]] = field(default_factory=dict)  # global target -> resolution descriptors seen

    def add(self, src: str, kind: str, target: str) -> None:
        self.edges.append((src, kind, target))
        self.reverse.setdefault(target, set()).add(src)

    def stats(self) -> dict[str, int]:
        kinds: dict[str, int] = {}
        for _, k, _ in self.edges:
            kinds[k] = kinds.get(k, 0) + 1
        return {"edges": len(self.edges), "dynamic_edges": len(self.dynamic), **{f"{k}_edges": v for k, v in sorted(kinds.items())}}


def global_node(module: str, name: str) -> str:
    return f"global:{module}::{name}"


def build_graph(lowered: Any) -> DepGraph:
    g = DepGraph()
    for m in lowered.modules:
        for fn in m.functions:
            for d in fn.deps:
                if d["kind"] == "function_body":
                    g.add(fn.symbol_id, "calls", d["symbol"])
                elif d["kind"] == "global":
                    t = global_node(d["module"], d["name"])
                    g.add(fn.symbol_id, "references", t)
                    g.descriptors.setdefault(t, set()).add(f"{d.get('resolution')}|{d.get('descriptor')}")
            for u in fn.unresolved_calls:
                g.dynamic.append((fn.symbol_id, u))
        for us in [*m.functions, *m.unsupported_symbols]:
            # Conservative reads (impact only: changed module data, import targets and
            # class bodies reach every reader, supported or not).
            for d in us.reads:
                if d["kind"] == "global":
                    t = global_node(d["module"], d["name"])
                    g.add(us.symbol_id, "references", t)
                    g.descriptors.setdefault(t, set()).add(f"{d.get('resolution')}|{d.get('descriptor')}")
    return g


def changed_globals(base_lowered: Any, cand_lowered: Any, files: FileChanges, gb: DepGraph | None = None, gc: DepGraph | None = None) -> set[str]:
    """Global nodes whose module bindings differ between the snapshots (both id spaces)."""
    out: set[str] = set()
    if gb is not None and gc is not None:
        # Resolution descriptors seen by dependents also cover import mappings and the
        # bindings of imported targets in other modules.
        rename_paths = dict(files.renamed)
        for t in set(gb.descriptors) | set(gc.descriptors):
            mod, _, name = t.removeprefix("global:").partition("::")
            other = global_node(rename_paths.get(mod, mod), name)
            if gb.descriptors.get(t, set()) != gc.descriptors.get(other, gc.descriptors.get(t, set())):
                out.add(t)
                out.add(other)
    rename = dict(files.renamed)
    cand_mods = cand_lowered.program.summaries
    base_mods = base_lowered.program.summaries
    for bpath, bsum in base_mods.items():
        cpath = rename.get(bpath, bpath)
        csum = cand_mods.get(cpath)
        for n in set(bsum) | (set(csum) if csum is not None else set()):
            if csum is None or bsum.get(n) != csum.get(n):
                out.add(global_node(bpath, n))
                out.add(global_node(cpath, n))
    for cpath, csum in cand_mods.items():
        if cpath in set(rename.values()) or cpath in base_mods:
            continue
        for n in csum:
            out.add(global_node(cpath, n))  # new module: every binding is new
    # A module that disappeared or appeared also changes how imports of it resolve.
    for p in set(base_mods) ^ set(cand_mods):
        out.add(f"module:{p}")
    return out


@dataclass
class Slice:
    affected: set[str]  # candidate symbol ids
    base_affected: set[str]  # base symbol ids
    reasons: dict[str, str]  # candidate symbol -> why it was invalidated
    widened_by_key_check: list[str]
    global_change: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "affected_symbols": len(self.affected),
            "base_symbols_rechecked": len(self.base_affected),
            "global_change": self.global_change,
            "widened_by_key_check": sorted(self.widened_by_key_check),
        }


def _walk(graph: DepGraph, seeds: dict[str, str]) -> dict[str, str]:
    """Reverse reachability from seed targets; returns symbol -> reason."""
    out: dict[str, str] = {}
    q = deque(seeds.items())
    while q:
        target, why = q.popleft()
        for src in sorted(graph.reverse.get(target, ())):
            if src in out:
                continue
            kind = "calls" if not target.startswith("global:") else "reads"
            out[src] = f"{kind} {target.removeprefix('global:')} ({why})"
            q.append((src, "transitively"))
    return out


def affected_slice(base_lowered: Any, cand_lowered: Any, base_syms: dict[str, Sym], cand_syms: dict[str, Sym], match: SymbolMatch,
                   files: FileChanges, global_change: str | None) -> Slice:
    reasons: dict[str, str] = {}
    if global_change is not None:
        for c in cand_syms:
            reasons[c] = f"global invalidation: {global_change}"
        return Slice(set(cand_syms), set(base_syms), reasons, [], global_change)
    for c in match.added:
        reasons[c] = "new or unmatched symbol"
    for c in match.modified:
        reasons[c] = "definition changed"
    for c in match.uncertain:
        reasons.setdefault(c, "possible rename (uncertain match)")
    for c, (b, how) in match.cand_to_base.items():
        if how != "anchor":
            reasons.setdefault(c, f"renamed from {b} (symbol identity changed)")
    gb = build_graph(base_lowered)
    gc = build_graph(cand_lowered)
    cg = changed_globals(base_lowered, cand_lowered, files, gb, gc)
    b2c = match.base_to_cand()
    cand_seeds = {c: "changed" for c in reasons} | {t: "binding changed" for t in cg}
    base_seeds = {b: "deleted" for b in match.deleted} | {b2c_inv: "changed" for b2c_inv in (match.cand_to_base[c][0] for c in match.modified)} | {t: "binding changed" for t in cg}
    for sym, why in _walk(gc, cand_seeds).items():
        reasons.setdefault(sym, why)
    base_hit = _walk(gb, base_seeds)
    for bsym, why in base_hit.items():
        c = b2c.get(bsym)
        if c is not None:
            reasons.setdefault(c, f"base dependency: {why}")
    # Fail-closed cross-check: a changed validity key must be in the slice.
    widened: list[str] = []
    for c, (b, _) in match.cand_to_base.items():
        if cand_syms[c].key != base_syms[b].key and c not in reasons:
            reasons[c] = "validity key changed (graph walk missed this dependency; added by the cross-check)"
            widened.append(c)
    affected = set(reasons)
    base_affected = {match.cand_to_base[c][0] for c in affected if c in match.cand_to_base} | set(match.deleted) | set(base_hit) | set(match.uncertain.values())
    return Slice(affected, base_affected, reasons, widened, None)
