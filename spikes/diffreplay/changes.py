"""Commit selection, snapshots and changed-function detection (reusing invariantguard)."""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from invariantguard.config import Config
from invariantguard.frontends.python.resolve import ast_fingerprint
from invariantguard.impact.graph import diff_files, match_symbols, symbol_table
from invariantguard.impact.keys import snapshot_keys
from invariantguard.orchestration import SEMANTICS_PROFILE, lower_snapshot
from invariantguard.snapshots.capture import Snapshot, capture_git_revision
from invariantguard.snapshots.git import GitRepo

CORPUS = Path(__file__).resolve().parents[1] / "corpus"

REPOS: dict[str, dict[str, Any]] = {
    "more-itertools": {"pkg_roots": ["more_itertools"], "pathspec": ["more_itertools/*.py"], "exclude": []},
    "boltons": {"pkg_roots": ["boltons"], "pathspec": ["boltons/*.py"], "exclude": []},
    "dateutil": {"pkg_roots": ["src/dateutil", "dateutil"], "pathspec": ["src/dateutil/*.py", "dateutil/*.py", ":(exclude)dateutil/test/*",
                                                                         ":(exclude)src/dateutil/test/*"], "exclude": ["test"]},
}


def repo(name: str) -> GitRepo:
    return GitRepo(CORPUS / name)


def recent_commits(name: str, n: int = 30) -> list[dict[str, str]]:
    r = repo(name)
    spec = REPOS[name]["pathspec"]
    out = r._run(["log", "--no-merges", "-n", str(n), "--format=%H%x1f%P%x1f%s%x1f%ci", "--", *spec], cwd=r.toplevel).decode()
    commits = []
    for line in out.splitlines():
        h, parents, subj, date = line.split("\x1f")
        commits.append({"commit": h, "parent": parents.split()[0] if parents else "", "subject": subj, "date": date})
    return commits


def message(name: str, commit: str) -> str:
    r = repo(name)
    return r._run(["log", "-1", "--format=%B", commit], cwd=r.toplevel).decode()


def diff_text(name: str, commit: str, paths: list[str]) -> str:
    r = repo(name)
    return r._run(["show", "--no-ext-diff", "--format=", commit, "--", *paths], cwd=r.toplevel).decode("utf-8", "replace")


def snapshot(name: str, commit: str) -> Snapshot:
    r = repo(name)
    return capture_git_revision(r, commit, r.toplevel, ["."], [".git", "docs"], SEMANTICS_PROFILE)


def pkg_info(name: str, files: dict[str, bytes]) -> tuple[str, str]:
    """(package dir relative to repo root, python_path root) for this snapshot's layout."""
    for root in REPOS[name]["pkg_roots"]:
        if f"{root}/__init__.py" in files:
            parent = str(Path(root).parent)
            return root, parent if parent != "." else "."
    raise RuntimeError(f"no package root found for {name}")


def module_of(path: str, pyroot: str) -> str:
    rel = path if pyroot == "." else path[len(pyroot) + 1:]
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# -- function inventory ----------------------------------------------------------------------
@dataclass
class FnDef:
    qualname: str
    node: ast.AST
    kind: str  # function | method | staticmethod | classmethod | property | async
    fp: str
    fp_nodoc: str
    params: list[str]
    class_name: str | None = None
    decorators: list[str] = field(default_factory=list)


def _strip_doc(node: ast.AST) -> ast.AST:
    n = copy.deepcopy(node)
    for sub in ast.walk(n):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            b = sub.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) and isinstance(b[0].value.value, str):
                sub.body = b[1:] or [ast.Pass()]
    return n


def inventory(src: bytes) -> dict[str, FnDef]:
    tree = ast.parse(src)
    out: dict[str, FnDef] = {}

    def visit(body: list[ast.stmt], prefix: str, cls: str | None) -> None:
        for st in body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decos = [ast.unparse(d) for d in st.decorator_list]
                kind = "async" if isinstance(st, ast.AsyncFunctionDef) else "function" if cls is None else "method"
                if cls is not None:
                    if "staticmethod" in decos:
                        kind = "staticmethod"
                    elif "classmethod" in decos:
                        kind = "classmethod"
                    elif any(d == "property" or d.endswith(".setter") or d.endswith(".getter") for d in decos):
                        kind = "property"
                a = st.args
                params = [p.arg for p in a.posonlyargs + a.args + a.kwonlyargs]
                q = prefix + st.name
                if q in out:  # redefinition (e.g. property setter): keep both distinct
                    q = q + "#2"
                out[q] = FnDef(q, st, kind, ast_fingerprint(st), ast_fingerprint(_strip_doc(st)), params, cls, decos)
            elif isinstance(st, ast.ClassDef):
                visit(st.body, prefix + st.name + ".", prefix + st.name)
            elif isinstance(st, (ast.If, ast.Try)):
                # conditionally defined functions at module level (e.g. py2/py3 branches)
                for blk in [st.body, getattr(st, "orelse", [])] + [h.body for h in getattr(st, "handlers", [])]:
                    visit(blk, prefix, cls)

    visit(tree.body, "", None)
    return out


@dataclass
class Change:
    path: str
    qualname: str
    status: str  # modified | added | deleted
    doc_only: bool
    kind: str
    params_base: list[str] | None
    params_patch: list[str] | None


def changed_functions(base: Snapshot, patch: Snapshot, pkg_dir: str, exclude_dirs: list[str]) -> tuple[list[Change], dict[str, Any]]:
    files = diff_files(base, patch)
    bm, pm = base.file_map(), patch.file_map()
    changes: list[Change] = []
    renamed_to = set(files.renamed.values())
    paths = [p for p in files.modified + files.added if p.startswith(pkg_dir + "/") and not any(f"/{d}/" in p for d in exclude_dirs)]
    for p in sorted(paths):
        try:
            pi = inventory(pm[p].raw)
        except SyntaxError:
            continue
        old_path = next((o for o, n in files.renamed.items() if n == p), p)
        bi = {}
        if old_path in bm:
            try:
                bi = inventory(bm[old_path].raw)
            except SyntaxError:
                bi = {}
        for q, d in pi.items():
            b = bi.get(q)
            if b is None:
                changes.append(Change(p, q, "added", False, d.kind, None, d.params))
            elif b.fp != d.fp:
                changes.append(Change(p, q, "modified", b.fp_nodoc == d.fp_nodoc, d.kind, b.params, d.params))
        for q, b in bi.items():
            if q not in pi:
                changes.append(Change(p, q, "deleted", False, b.kind, b.params, None))
    meta = {"files": files.to_json(), "renamed_to": sorted(renamed_to)}
    return changes, meta


def existing_impact_modified(base: Snapshot, patch: Snapshot) -> dict[str, Any]:
    """What invariantguard's own impact layer (lower + match_symbols) calls modified."""
    cfg = Config()
    lb, lp = lower_snapshot(base, cfg), lower_snapshot(patch, cfg)
    _, kb = snapshot_keys(lb, cfg, "spike", SEMANTICS_PROFILE)
    _, kp = snapshot_keys(lp, cfg, "spike", SEMANTICS_PROFILE)
    sb, sp = symbol_table(lb, kb), symbol_table(lp, kp)
    m = match_symbols(sb, sp, diff_files(base, patch))
    key_changed = sorted(c for c, (b, _) in m.cand_to_base.items() if kb.get(b) != kp.get(c))
    return {"modified": sorted(m.modified), "added": sorted(m.added), "key_changed": key_changed,
            "kinds": {s: sp[s].kind for s in m.modified + m.added}}


# -- intra-module callers (impact through unchanged callers) -------------------------------------------
def _calls_target(node: ast.AST, qualname: str, cls_of_node: str | None, instances: dict[str, str] | None = None) -> bool:
    parts = qualname.split(".")
    name = parts[-1]
    tcls = ".".join(parts[:-1]) if len(parts) > 1 else None
    for n in ast.walk(node):
        if tcls is None:
            if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load):
                return True
        else:
            if isinstance(n, ast.Attribute) and n.attr == name:
                v = n.value
                if isinstance(v, ast.Name) and (v.id == tcls.split(".")[-1] or (v.id in ("self", "cls") and cls_of_node == tcls)
                                                or (instances or {}).get(v.id) == tcls):
                    return True
                if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "super":
                    return True
            if name == "__init__" and isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == tcls.split(".")[-1]:
                return True
    return False


def callers(inv: dict[str, FnDef], qualname: str, depth: int = 2, limit: int = 8, instances: dict[str, str] | None = None) -> list[tuple[str, int]]:
    """Functions/methods of the same module that (transitively) reference ``qualname``; (qualname, distance)."""
    out: list[tuple[str, int]] = []
    frontier = [qualname]
    seen = {qualname}
    for d in range(1, depth + 1):
        nxt = []
        for t in frontier:
            for q, fd in inv.items():
                if q in seen or "#" in q:
                    continue
                if _calls_target(fd.node, t, fd.class_name, instances):
                    seen.add(q)
                    nxt.append(q)
                    out.append((q, d))
        frontier = nxt
    out.sort(key=lambda x: (x[1], x[0].split(".")[-1].startswith("_"), x[0]))
    return out[:limit]
