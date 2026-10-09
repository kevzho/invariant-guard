"""Read-only Git access for base/candidate snapshots (docs/07).

Rules enforced here:

* Every command is an argument array passed to ``subprocess.run`` without a shell. No
  filename or reference is ever interpolated into a command string. References are
  passed after ``--end-of-options`` and must not start with ``-``.
* Only plumbing that reads objects, the index or ignore rules is used:
  ``rev-parse``, ``ls-tree``, ``cat-file --batch`` (no ``--filters``/``--textconv``, so no
  smudge/clean/textconv program runs), ``ls-files``. Nothing checks out, resets,
  stashes, commits or writes refs.
* ``GIT_OPTIONAL_LOCKS=0`` stops read commands from refreshing and rewriting
  ``.git/index``. ``-c`` overrides disable ``core.fsmonitor`` (which runs a configured
  program), hooks, external diff drivers and the untracked cache; replace refs are
  ignored so object reads are immutable. System config is not read.
* Working-tree blob ids are computed in Python from the raw bytes (no ``hash-object``,
  which would apply clean filters).

The engine's static boundary (no execution of repository code) is unaffected: Git is a
trusted local tool reading repository metadata, and the scan test allowlists only this
module for ``subprocess``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

GIT_TIMEOUT_S = 60
_SAFE_CONFIG = (
    "-c", "core.fsmonitor=false",
    "-c", "core.untrackedCache=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "diff.external=",
    "-c", "core.quotePath=false",
    "-c", "protocol.allow=never",
)


class GitError(Exception):
    """Git is unavailable, the path is not a repository, or a read failed."""


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    kind: str  # blob | tree | commit (submodule)
    oid: str
    path: str  # relative to the Git top level, POSIX


@dataclass
class IndexEntry:
    mode: str
    oid: str
    stage: int
    path: str


@dataclass
class GitStatus:
    """How each path of the working tree differs from HEAD and the index (disclosure)."""

    head: str | None
    states: dict[str, list[str]] = field(default_factory=dict)  # path -> labels


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT", "USER", "LOGNAME")}
    env.update({
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ADVICE": "0",
        "LC_ALL": "C",
    })
    return env


def git_executable() -> str | None:
    return shutil.which("git")


def _check_ref(ref: str) -> None:
    if not ref or ref.startswith("-") or "\x00" in ref or any(c in ref for c in "\n\r"):
        raise GitError(f"invalid Git reference {ref!r}")


class GitRepo:
    """A repository located at or above ``path``; all reads are side-effect free."""

    def __init__(self, path: Path) -> None:
        exe = git_executable()
        if exe is None:
            raise GitError("git executable not found on PATH")
        self.exe = exe
        start = path if path.is_dir() else path.parent
        out = self._run(["rev-parse", "--show-toplevel"], cwd=start)
        self.toplevel = Path(os.fsdecode(out.strip())).resolve()
        fmt = self._run(["rev-parse", "--show-object-format"], cwd=self.toplevel).strip().decode() or "sha1"
        if fmt not in ("sha1", "sha256"):
            raise GitError(f"unsupported object format {fmt!r}")
        self.object_format = fmt
        ver = self._run(["--version"], cwd=self.toplevel).strip().decode()
        self.version = ver

    # -- process ----------------------------------------------------------------------
    def _run(self, args: list[str], cwd: Path, stdin: bytes | None = None) -> bytes:
        argv = [self.exe, "--no-pager", *_SAFE_CONFIG, *args]
        try:
            proc = subprocess.run(argv, cwd=str(cwd), env=_env(), input=stdin, capture_output=True, timeout=GIT_TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitError(f"git {args[0]} failed: {type(exc).__name__}: {exc}") from None
        if proc.returncode != 0:
            msg = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise GitError(f"git {args[0]} failed (exit {proc.returncode}): {msg[-1] if msg else 'no message'}")
        return proc.stdout

    # -- objects -----------------------------------------------------------------------
    def resolve_commit(self, ref: str) -> str:
        _check_ref(ref)
        try:
            out = self._run(["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"], cwd=self.toplevel)
        except GitError:
            raise GitError(f"reference {ref!r} does not name a commit") from None
        oid = out.strip().decode()
        if not oid:
            raise GitError(f"reference {ref!r} does not name a commit")
        return oid

    def head(self) -> str | None:
        try:
            return self.resolve_commit("HEAD")
        except GitError:
            return None  # unborn branch

    def has_commit(self, oid: str) -> bool:
        try:
            return self.resolve_commit(oid) == oid
        except GitError:
            return False

    def ls_tree(self, commit: str) -> list[TreeEntry]:
        _check_ref(commit)
        raw = self._run(["ls-tree", "-r", "-z", "--full-tree", "--end-of-options", commit], cwd=self.toplevel)
        out: list[TreeEntry] = []
        for rec in raw.split(b"\x00"):
            if not rec:
                continue
            meta, _, path = rec.partition(b"\t")
            mode, kind, oid = meta.decode().split(" ")
            out.append(TreeEntry(mode, kind, oid, os.fsdecode(path)))
        return out

    def read_blobs(self, oids: list[str]) -> dict[str, bytes]:
        """Raw blob contents via ``cat-file --batch`` (no filters, no textconv)."""
        uniq = sorted(set(oids))
        if not uniq:
            return {}
        raw = self._run(["cat-file", "--batch"], cwd=self.toplevel, stdin=("\n".join(uniq) + "\n").encode())
        out: dict[str, bytes] = {}
        pos = 0
        for oid in uniq:
            nl = raw.index(b"\n", pos)
            header = raw[pos:nl].decode().split(" ")
            if len(header) != 3 or header[0] != oid or header[1] != "blob":
                raise GitError(f"unexpected cat-file response for {oid}: {' '.join(header)}")
            size = int(header[2])
            start = nl + 1
            out[oid] = raw[start:start + size]
            pos = start + size + 1
        for oid, data in out.items():
            if self.blob_oid(data) != oid:
                raise GitError(f"blob {oid} content does not match its id")
        return out

    def blob_oid(self, data: bytes) -> str:
        h = hashlib.sha1() if self.object_format == "sha1" else hashlib.sha256()
        h.update(b"blob %d\x00" % len(data))
        h.update(data)
        return h.hexdigest()

    # -- index and working tree --------------------------------------------------------
    def index_entries(self) -> list[IndexEntry]:
        raw = self._run(["ls-files", "-z", "--stage"], cwd=self.toplevel)
        out: list[IndexEntry] = []
        for rec in raw.split(b"\x00"):
            if not rec:
                continue
            meta, _, path = rec.partition(b"\t")
            mode, oid, stage = meta.decode().split(" ")
            out.append(IndexEntry(mode, oid, int(stage), os.fsdecode(path)))
        return out

    def untracked(self, ignored: bool = False) -> list[str]:
        args = ["ls-files", "-z", "--others", "--exclude-standard"] + (["--ignored"] if ignored else [])
        raw = self._run(args, cwd=self.toplevel)
        return sorted(os.fsdecode(p) for p in raw.split(b"\x00") if p)

    def worktree_listing(self) -> tuple[set[str], set[str]]:
        """(tracked paths in the index, untracked non-ignored paths), top-level relative."""
        tracked = {e.path for e in self.index_entries()}
        return tracked, set(self.untracked())

    def status(self, paths: set[str] | None = None) -> GitStatus:
        """Per-path staged/unstaged/untracked/deleted labels relative to HEAD and the index."""
        head = self.head()
        head_blobs = {e.path: e.oid for e in self.ls_tree(head) if e.kind == "blob"} if head else {}
        index = {}
        conflicted: set[str] = set()
        for e in self.index_entries():
            if e.stage != 0:
                conflicted.add(e.path)
            else:
                index[e.path] = e.oid
        untracked = set(self.untracked())
        st = GitStatus(head=head)
        universe = set(head_blobs) | set(index) | untracked | conflicted
        if paths is not None:
            universe &= paths
        for p in sorted(universe):
            labels: list[str] = []
            full = self.toplevel / p
            on_disk = full.is_file() and not full.is_symlink()
            if p in conflicted:
                labels.append("conflicted")
            if p in untracked:
                labels.append("untracked")
            elif p in index:
                if head_blobs.get(p) != index[p]:
                    labels.append("staged" if p in head_blobs else "staged_new")
                if not on_disk:
                    labels.append("deleted_unstaged")
                else:
                    try:
                        if self.blob_oid(full.read_bytes()) != index[p]:
                            labels.append("unstaged")
                    except OSError:
                        labels.append("unreadable")
            elif p in head_blobs:
                labels.append("deleted_staged")
            if labels:
                st.states[p] = labels
        return st
