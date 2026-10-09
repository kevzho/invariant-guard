"""Immutable, content-addressed source snapshots (docs/07 "Snapshot contract").

* ``capture``: the working tree. File bytes are read exactly once and analysis uses
  those in-memory copies. In a Git repository the candidate file set is the index plus
  untracked, non-ignored files (so staged, unstaged, untracked, deleted and renamed files
  all participate) read from disk.
* ``capture_git_revision``: a commit, read from Git objects without touching the
  working tree.
* ``capture_from_blobs``: a saved baseline whose source was explicitly retained.

``verify_unchanged`` re-hashes every captured file *and* re-lists the scope, so an edit,
a new file or a deletion after capture is detected. The orchestrator restarts the run
once on a fresh capture and reports INCOMPLETE if the tree changes again.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..models.spans import digest_json, sha256_bytes

SNAPSHOT_SCHEMA_VERSION = "1.0"
MAX_FILE_BYTES = 5 * 1024 * 1024
# Dependency/environment lock files at the analysis root (docs/07: lock changes can
# invalidate every summary). Their digests are part of the snapshot identity.
LOCK_PATTERNS = ("requirements*.txt", "requirements*.lock", "constraints*.txt", "*.lock", "pyproject.toml", "setup.cfg", "Pipfile")
CONFIG_REL = ".guard/config.toml"


@dataclass
class CapturedFile:
    path: str  # POSIX path relative to root
    raw: bytes
    digest: str


@dataclass
class Snapshot:
    root: Path
    files: list[CapturedFile]
    excluded: list[dict[str, str]]
    config_digest: str | None
    semantics_profile: str
    scope: list[str]
    snapshot_id: str = ""
    consistent: bool = True
    changed_paths: list[str] = field(default_factory=list)
    # In-scope sources that could not be captured for analysis (Python files that are
    # symlinks, too large, unreadable or have unrepresentable paths, and explicitly
    # targeted non-Python files). Each is required coverage that was not performed, so the
    # run cannot be COMPLETE (audit A-04). Also listed in ``excluded`` for the manifest.
    uncaptured: list[dict[str, str]] = field(default_factory=list)
    kind: str = "working_tree_capture"
    source_identity: dict[str, Any] | None = None
    dependency_locks: list[dict[str, str]] = field(default_factory=list)
    # Re-lists in-scope Python paths for the consistency check (working tree only).
    relist: Callable[[], set[str]] | None = None
    source_retained: bool = False

    def manifest(self) -> dict[str, Any]:
        m = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "snapshot_id": self.snapshot_id,
            "kind": self.kind,
            "root_label": self.root.name,
            "scope": list(self.scope),
            "files": [{"path": f.path, "sha256": f.digest, "size_bytes": len(f.raw)} for f in self.files],
            "excluded": list(self.excluded),
            "config_digest": self.config_digest,
            "semantics_profile": self.semantics_profile,
            "dependency_lock_digests": [d["sha256"] for d in self.dependency_locks],
            "dependency_locks": list(self.dependency_locks),
            "source_retained": self.source_retained,
        }
        if self.source_identity is not None:
            m["source_identity"] = dict(self.source_identity)
        return m

    def file_map(self) -> dict[str, CapturedFile]:
        return {f.path: f for f in self.files}


def _identity_payload(s: Snapshot) -> dict[str, Any]:
    m = s.manifest()
    m.pop("snapshot_id")
    m.pop("source_retained")
    return m


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def _label(rel: str) -> str:
    """A UTF-8-safe label for a path that may contain undecodable bytes."""
    return os.fsencode(rel).decode("utf-8", "backslashreplace")


def path_problem(rel: str) -> str | None:
    """Why ``rel`` cannot serve as a report path / symbol-ID prefix, or None (audit A-05)."""
    try:
        rel.encode("utf-8")
    except UnicodeEncodeError:
        return "path is not valid UTF-8"
    if ":" in rel:
        return "path contains ':' (reserved as the symbol-ID separator)"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in rel):
        return "path contains control characters"
    if any(part[:1].isspace() for part in rel.split("/")):
        return "path component starts with whitespace"
    return None


def in_scope(rel: str, scope: list[str]) -> bool:
    return any(s == "." or rel == s or rel.startswith(s.rstrip("/") + "/") for s in scope)


def excluded_dir(rel: str, exclude: set[str]) -> str | None:
    parts = rel.split("/")[:-1]
    for p in parts:
        if p in exclude:
            return p
    return None


def finalize(snap: Snapshot) -> Snapshot:
    snap.files.sort(key=lambda f: f.path)
    snap.excluded.sort(key=lambda e: e["path"])
    snap.uncaptured.sort(key=lambda e: e["path"])
    snap.dependency_locks.sort(key=lambda d: d["path"])
    snap.snapshot_id = digest_json(_identity_payload(snap))
    return snap


def lock_files_on_disk(root: Path) -> list[dict[str, str]]:
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        p = root / name
        if any(fnmatch.fnmatch(name, pat) for pat in LOCK_PATTERNS) and p.is_file() and not p.is_symlink():
            try:
                out.append({"path": name, "sha256": sha256_bytes(p.read_bytes())})
            except OSError:
                continue
    return out


def capture(
    root: Path,
    targets: list[Path],
    exclude: list[str],
    config_digest: str | None,
    semantics_profile: str,
    *,
    listing: Callable[[], set[str]] | None = None,
    git_note: dict[str, Any] | None = None,
) -> Snapshot:
    """Capture the working tree under ``targets``.

    ``listing`` (Git mode) returns the root-relative candidate paths (index entries plus
    untracked, non-ignored files); without it the directory tree is walked.
    """
    root = root.resolve()
    files: list[CapturedFile] = []
    excluded: list[dict[str, str]] = []
    seen: set[str] = set()
    exclude_set = set(exclude)

    uncaptured: list[dict[str, str]] = []

    def skip(rel: str, reason: str) -> None:
        entry = {"path": _label(rel), "reason": reason}
        excluded.append(entry)
        uncaptured.append(dict(entry))

    def add_file(p: Path) -> None:
        rel = _rel(root, p)
        if rel in seen:
            return
        seen.add(rel)
        problem = path_problem(rel)
        if problem is not None:
            skip(rel, f"Python source not analyzed: {problem}")
            return
        if p.is_symlink():
            skip(rel, "Python source not analyzed: symlink (not followed)")
            return
        try:
            size = p.stat().st_size
            if size > MAX_FILE_BYTES:
                skip(rel, f"Python source not analyzed: file larger than {MAX_FILE_BYTES} bytes")
                return
            raw = p.read_bytes()
        except OSError as exc:
            skip(rel, f"Python source not analyzed: unreadable: {exc.strerror}")
            return
        files.append(CapturedFile(rel, raw, sha256_bytes(raw)))

    scope: list[str] = []
    for target in targets:
        t = target.resolve() if not target.is_symlink() else target.absolute()
        try:
            rel = _rel(root, t) if t != root else "."
        except ValueError:
            raise ValueError(f"target {target} is outside the repository root {root}") from None
        scope.append(rel)
        if t.is_symlink() and t.suffix == ".py":
            add_file(t)  # records the symlink as an uncaptured required source
            continue
        if t.is_file():
            if t.suffix == ".py":
                add_file(t)
            else:
                # The user asked for this file explicitly; nothing was analyzed for it.
                skip(rel, "explicitly targeted file is not a Python source file (.py); nothing was analyzed")
            continue
        if listing is not None:
            for relp in sorted(listing()):
                if not relp.endswith(".py") or not in_scope(relp, [rel]):
                    continue
                d = excluded_dir(relp, exclude_set)
                if d is not None:
                    continue
                p = root / relp
                if not p.exists() and not p.is_symlink():
                    continue  # tracked but deleted in the working tree
                add_file(p)
            continue
        for dirpath, dirnames, filenames in os.walk(t, followlinks=False):
            d = Path(dirpath)
            keep = []
            for name in sorted(dirnames):
                full = d / name
                if name in exclude_set:
                    continue
                if full.is_symlink():
                    excluded.append({"path": _rel(root, full), "reason": "symlinked directory (not followed)"})
                    continue
                keep.append(name)
            dirnames[:] = keep
            for name in sorted(filenames):
                if name.endswith(".py"):
                    add_file(d / name)
    snap = Snapshot(root, files, excluded, config_digest, semantics_profile, sorted(_label(x) for x in scope))
    snap.uncaptured = uncaptured
    snap.dependency_locks = lock_files_on_disk(root)
    if git_note is not None:
        snap.source_identity = dict(git_note)
    scope_copy = list(snap.scope)

    def relist() -> set[str]:
        if listing is not None:
            return {p for p in listing() if p.endswith(".py") and in_scope(p, scope_copy) and excluded_dir(p, exclude_set) is None
                    and (root / p).exists()}
        out: set[str] = set()
        for s in scope_copy:
            t = root if s == "." else root / s
            if t.is_file():
                if t.suffix == ".py":
                    out.add(_rel(root, t))
                continue
            for dirpath, dirnames, filenames in os.walk(t, followlinks=False):
                dirnames[:] = [n for n in sorted(dirnames) if n not in exclude_set and not (Path(dirpath) / n).is_symlink()]
                out.update(_rel(root, Path(dirpath) / n) for n in filenames if n.endswith(".py"))
        return out

    snap.relist = relist
    return finalize(snap)


def capture_git_revision(repo: Any, commit: str, root: Path, scope: list[str], exclude: list[str], semantics_profile: str) -> Snapshot:
    """Snapshot of ``commit`` restricted to ``root``/``scope`` from Git objects only."""
    root = root.resolve()
    try:
        prefix = root.relative_to(repo.toplevel).as_posix()
    except ValueError:
        raise ValueError(f"analysis root {root} is outside the Git work tree {repo.toplevel}") from None
    prefix = "" if prefix == "." else prefix + "/"
    exclude_set = set(exclude)
    files: list[CapturedFile] = []
    excluded: list[dict[str, str]] = []
    uncaptured: list[dict[str, str]] = []
    locks: list[dict[str, str]] = []
    want: dict[str, str] = {}
    config_oid: str | None = None
    lock_oids: dict[str, str] = {}
    for e in repo.ls_tree(commit):
        if prefix and not e.path.startswith(prefix):
            continue
        rel = e.path[len(prefix):]
        if rel == CONFIG_REL and e.kind == "blob":
            config_oid = e.oid
        if "/" not in rel and e.kind == "blob" and any(fnmatch.fnmatch(rel, pat) for pat in LOCK_PATTERNS):
            lock_oids[rel] = e.oid
        if not rel.endswith(".py") or not in_scope(rel, scope):
            if e.kind == "commit" and in_scope(rel, scope):
                excluded.append({"path": _label(rel), "reason": "Git submodule (gitlink) is not analyzed"})
            continue
        if excluded_dir(rel, exclude_set) is not None:
            continue
        problem = path_problem(rel)
        if problem is not None:
            entry = {"path": _label(rel), "reason": f"Python source not analyzed: {problem}"}
            excluded.append(entry)
            uncaptured.append(dict(entry))
            continue
        if e.mode == "120000":
            entry = {"path": _label(rel), "reason": "Python source not analyzed: symlink in the Git tree (not followed)"}
            excluded.append(entry)
            uncaptured.append(dict(entry))
            continue
        if e.kind != "blob":
            continue
        want[rel] = e.oid
    blobs = repo.read_blobs(list(want.values()) + ([config_oid] if config_oid else []) + list(lock_oids.values()))
    for rel, oid in want.items():
        raw = blobs[oid]
        if len(raw) > MAX_FILE_BYTES:
            entry = {"path": _label(rel), "reason": f"Python source not analyzed: file larger than {MAX_FILE_BYTES} bytes"}
            excluded.append(entry)
            uncaptured.append(dict(entry))
            continue
        files.append(CapturedFile(rel, raw, sha256_bytes(raw)))
    for rel, oid in lock_oids.items():
        locks.append({"path": rel, "sha256": sha256_bytes(blobs[oid])})
    config_digest = sha256_bytes(blobs[config_oid]) if config_oid else None
    snap = Snapshot(root, files, excluded, config_digest, semantics_profile, sorted(scope))
    snap.uncaptured = uncaptured
    snap.kind = "git_revision"
    snap.dependency_locks = locks
    snap.source_identity = {"kind": "git_commit", "commit": commit, "object_format": repo.object_format}
    return finalize(snap)


def capture_from_blobs(root: Path, manifest: dict[str, Any], blobs: dict[str, bytes], label: str, semantics_profile: str) -> Snapshot:
    """Rebuild a saved baseline snapshot from retained source (digests are re-verified)."""
    files = []
    for f in manifest["files"]:
        raw = blobs.get(f["sha256"])
        if raw is None or sha256_bytes(raw) != f["sha256"]:
            raise ValueError(f"retained source for {f['path']} is missing or corrupted")
        files.append(CapturedFile(f["path"], raw, f["sha256"]))
    snap = Snapshot(root.resolve(), files, list(manifest.get("excluded", [])), manifest.get("config_digest"), semantics_profile, list(manifest["scope"]))
    snap.kind = "saved_baseline"
    snap.dependency_locks = list(manifest.get("dependency_locks", []))
    snap.source_identity = {"kind": "baseline", "label": label}
    snap.source_retained = True
    snap.uncaptured = [e for e in snap.excluded if e.get("reason", "").startswith("Python source not analyzed")]
    return finalize(snap)


def verify_unchanged(snap: Snapshot) -> list[str]:
    """Paths whose content changed, appeared or disappeared since capture."""
    changed = []
    for f in snap.files:
        p = snap.root / f.path
        try:
            if sha256_bytes(p.read_bytes()) != f.digest:
                changed.append(f.path)
        except OSError:
            changed.append(f.path)
    if snap.relist is not None:
        try:
            now = snap.relist()
        except Exception as exc:  # noqa: BLE001 - a failing re-list means consistency is unknown
            changed.append(f"<listing failed: {type(exc).__name__}>")
            now = None
        if now is not None:
            known = {f.path for f in snap.files} | {e["path"] for e in snap.uncaptured}
            for p in sorted(now - known):
                changed.append(p)  # appeared mid-run
            for p in sorted({f.path for f in snap.files} - now):
                if p not in changed:
                    changed.append(p)  # deleted mid-run
    if lock_files_on_disk(snap.root) != snap.dependency_locks and snap.kind == "working_tree_capture":
        changed.append("<dependency lock files>")
    snap.changed_paths = changed
    snap.consistent = not changed
    return changed
