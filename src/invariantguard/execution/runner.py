"""Isolated probe runners with capability checks (docs/10, M3 T33/T34).

Backends:

* ``docker``: every probe batch runs in a fresh container started with
  ``--network none``, a read-only root filesystem, the snapshot copy and the harness
  bind-mounted read-only, a size-limited ``noexec`` tmpfs as the only scratch space,
  an unprivileged user with all capabilities dropped and ``no-new-privileges``, cgroup
  CPU/memory/PID limits, an RLIMIT_CPU ulimit and no environment passthrough (only
  ``HOME``, ``LANG``, ``PYTHONHASHSEED`` and the profile's explicit allowlist). The
  image is used by its local image ID with ``--pull never``; preparing it is the
  separate, explicit ``guard runner setup`` command. Before any target import, the
  harness checks each claimed capability *from inside a container started with the
  same flags*; a failed or unprobed required capability makes the backend
  RUNNER_UNAVAILABLE (fail closed). There is never a fallback to unisolated execution.
* ``unisolated``: only when the profile says so *and* ``execution.allow_unisolated =
  true``. It runs the harness in a local subprocess (``python -P -s -S -B``) with a
  minimal environment, per-probe CPU and wall limits and a temporary working copy, and
  reports every isolation capability as NOT_ENFORCED. Its evidence is labelled
  unisolated; it is never called sandboxed.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from ..models.spans import digest_json, sha256_bytes
from .process import ProcessResult, minimal_env, run_argv
from .profiles import RunProfileConfig

HARNESS_NAME = "probe_harness.py"
# Not -I: it implies -E, which ignores PYTHONHASHSEED (string hashing would be randomized per
# run). -P/-s give -I's path isolation; the environment is constructed explicitly instead, and
# the interpreter_env capability fails closed on interpreter variables we did not set.
HARNESS_FLAGS = ("-P", "-s", "-S", "-B")
PROTOCOL = "ig-probe-1"
# Harness kind -> (file, protocol). Contract probes and capability checks use ``probe``;
# differential replay uses ``replay`` (richer encoding, methods, repository inputs).
HARNESSES = {"probe": (HARNESS_NAME, PROTOCOL), "replay": ("replay_harness.py", "ig-replay-1")}
CONTAINER_SRC = "/work/src"
CONTAINER_HARNESS = "/work/harness"
OBSERVATION_STATUSES = ("ok", "import_error", "target_error", "timeout", "crashed", "output_limit", "harness_error")
_DOCKER_CANDIDATES = ("/usr/local/bin/docker", "/opt/homebrew/bin/docker", "/usr/bin/docker", "/Applications/Docker.app/Contents/Resources/bin/docker")


def harness_bytes(kind: str = "probe") -> bytes:
    return resources.files("invariantguard.execution").joinpath(HARNESSES[kind][0]).read_bytes()


def harness_digest(kind: str = "probe") -> str:
    return sha256_bytes(harness_bytes(kind))


class RunnerError(Exception):
    """A runner/environment failure (never a program defect)."""


@dataclass
class Capability:
    name: str
    required: bool
    status: str  # VERIFIED | FAILED | NOT_ENFORCED | NOT_PROBED
    detail: Any = None

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "required": self.required, "status": self.status, "detail": self.detail}


@dataclass
class BackendStatus:
    backend: str
    available: bool
    reason: str
    isolated: bool
    capabilities: list[Capability] = field(default_factory=list)
    environment: dict[str, Any] = field(default_factory=dict)
    environment_digest: str | None = None
    interpreter_version: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"backend": self.backend, "status": "AVAILABLE" if self.available else "RUNNER_UNAVAILABLE", "reason": self.reason,
                "isolated": self.isolated, "capabilities": [c.to_json() for c in self.capabilities], "environment": self.environment,
                "environment_digest": self.environment_digest}


class Workspace:
    """A host temporary directory with a copy of one snapshot and the harness.

    Only this directory is written by the host side of the runner. It is created with
    ``tempfile`` outside the repository and removed by ``close``.
    """

    def __init__(self, files: dict[str, bytes], label: str) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix=f"ig-{label}-")).resolve()
        try:
            self._materialize(files)
        except BaseException:
            shutil.rmtree(self.dir, ignore_errors=True)  # never leave a half-built workspace behind
            raise

    def _materialize(self, files: dict[str, bytes]) -> None:
        self.src = self.dir / "src"
        self.harness = self.dir / "harness"
        self.scratch = self.dir / "scratch"
        for d in (self.src, self.harness, self.scratch):
            d.mkdir(mode=0o755)
        for rel, raw in files.items():
            if rel.startswith("/") or ".." in rel.split("/") or "\x00" in rel:
                raise RunnerError(f"refusing to materialize unsafe path {rel!r}")
            p = self.src / rel
            p.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            if not str(p.resolve()).startswith(str(self.src) + os.sep):
                raise RunnerError(f"refusing to materialize {rel!r}: escapes the workspace")
            with open(p, "xb") as fh:
                fh.write(raw)
            os.chmod(p, 0o644)
        for kind, (fname, _) in HARNESSES.items():
            hp = self.harness / fname
            hp.write_bytes(harness_bytes(kind))
            os.chmod(hp, 0o644)
        for d in (self.dir, self.src, self.harness):
            os.chmod(d, 0o755)
        for dirpath, dirnames, _ in os.walk(self.src):
            for n in dirnames:
                os.chmod(os.path.join(dirpath, n), 0o755)

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


@dataclass
class HarnessRun:
    response: dict[str, Any] | None
    error: str | None
    process: ProcessResult | None
    container: str | None = None


def _parse_response(res: ProcessResult, mode: str, protocol: str = PROTOCOL) -> tuple[dict[str, Any] | None, str | None]:
    if res.start_error:
        return None, f"runner could not start: {res.start_error}"
    if res.timed_out:
        return None, "runner exceeded the profile's wall_seconds and was killed"
    if res.stdout_truncated:
        return None, "runner output exceeded max_output_bytes (killed)"
    if res.returncode != 0:
        tail = res.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        return None, f"runner exited with status {res.returncode}: {' | '.join(tail)[:400]}"
    try:
        resp = json.loads(res.stdout.decode("utf-8"))
    except RecursionError:
        # Untrusted: target code can forge its observation frame (audit A3-01).
        return None, "runner output is nested too deeply to parse (rejected)"
    except (UnicodeDecodeError, ValueError):
        return None, "runner produced output that is not one JSON document"
    if type(resp) is not dict or resp.get("protocol") != protocol:
        return None, f"runner response does not follow protocol {protocol}"
    if resp.get("mode") == "error":
        return None, f"harness error: {str(resp.get('error'))[:300]}"
    if resp.get("mode") != mode:
        return None, f"runner answered mode {resp.get('mode')!r}, expected {mode!r}"
    return resp, None


def validate_observations(resp: dict[str, Any], ids: list[str]) -> str | None:
    obs = resp.get("observations")
    if type(obs) is not list or [o.get("id") if type(o) is dict else None for o in obs] != ids:
        return "observations do not match the requested probes"
    for o in obs:
        if o.get("status") not in OBSERVATION_STATUSES:
            return f"unknown observation status {o.get('status')!r}"
    return None


class Backend:
    name = "abstract"
    isolated = False

    def __init__(self, profile: RunProfileConfig) -> None:
        self.profile = profile
        self._status: BackendStatus | None = None
        self.runs = 0

    def status(self) -> BackendStatus:  # pragma: no cover - interface
        raise NotImplementedError

    def src_root(self, ws: Workspace) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def run(self, ws: Workspace, request: dict[str, Any], harness: str = "probe") -> HarnessRun:  # pragma: no cover - interface
        raise NotImplementedError

    def probe_request(self, ws: Workspace, base: dict[str, Any]) -> dict[str, Any]:
        p = self.profile
        return {**base, "mode": "probe", "src_root": self.src_root(ws), "probe_wall_seconds": p.probe_wall_seconds,
                "probe_cpu_seconds": max(1, int(min(p.cpu_seconds, p.probe_wall_seconds * 2))), "max_result_bytes": p.max_result_bytes}


# -- docker -----------------------------------------------------------------------------------------
def find_docker(profile: RunProfileConfig) -> str | None:
    if profile.docker:
        return profile.docker if os.path.isfile(profile.docker) and os.access(profile.docker, os.X_OK) else None
    found = shutil.which("docker")
    if found:
        return found
    for c in _DOCKER_CANDIDATES:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


class DockerBackend(Backend):
    name = "docker"
    isolated = True

    def __init__(self, profile: RunProfileConfig) -> None:
        super().__init__(profile)
        self.exe = find_docker(profile)
        self.image_id: str | None = None
        self.image_env: list[str] = []

    def _env(self) -> dict[str, str]:
        path_dirs = [os.path.dirname(self.exe)] if self.exe else []
        path_dirs += ["/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin"]
        return minimal_env({"PATH": ":".join(path_dirs)}, passthrough=("HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY"))

    def cli(self, args: list[str], timeout_s: float = 30, stdin: bytes = b"", max_stdout: int = 1 << 20) -> ProcessResult:
        assert self.exe is not None
        return run_argv([self.exe, *args], stdin=stdin, env=self._env(), timeout_s=timeout_s, max_stdout=max_stdout)

    def server_version(self) -> tuple[str | None, str]:
        if self.exe is None:
            return None, "docker CLI not found (configure execution.profiles.<name>.docker or install Docker)"
        r = self.cli(["info", "--format", "{{json .ServerVersion}}"], timeout_s=20)
        if r.start_error or r.returncode != 0 or r.timed_out:
            msg = r.start_error or r.stderr.decode("utf-8", "replace").strip()[:300] or ("timed out" if r.timed_out else f"exit {r.returncode}")
            return None, f"docker daemon not available: {msg}"
        try:
            v = json.loads(r.stdout.decode().strip())
        except ValueError:
            return None, "docker info returned unexpected output"
        if not isinstance(v, str) or not v:
            return None, "docker info returned no server version"
        return v, "ok"

    def inspect_image(self) -> tuple[dict[str, Any] | None, str]:
        ref = self.profile.image or ""
        r = self.cli(["image", "inspect", "--format", "{{json .}}", "--", ref], timeout_s=30)
        if r.returncode != 0 or r.timed_out or r.start_error:
            return None, f"image {ref!r} is not present locally; prepare it explicitly with `guard runner setup --run-profile {self.profile.name}` (scans never pull)"
        try:
            info = json.loads(r.stdout.decode())
        except ValueError:
            return None, "docker image inspect returned unexpected output"
        repo_digests = info.get("RepoDigests") or []
        if self.profile.image_digest and not any(d.endswith("@" + self.profile.image_digest) for d in repo_digests):
            return None, f"local image {ref!r} does not match the pinned image_digest {self.profile.image_digest} (found {repo_digests})"
        img_id = info.get("Id", "")
        if not img_id.startswith("sha256:") or len(img_id) != 71:
            return None, "local image has no sha256 image ID"
        return {"image": ref, "image_id": img_id, "repo_digests": repo_digests, "os": info.get("Os"), "architecture": info.get("Architecture"),
                "env": (info.get("Config") or {}).get("Env") or []}, "ok"

    def src_root(self, ws: Workspace) -> str:
        return CONTAINER_SRC

    def container_env(self) -> dict[str, str]:
        env = {"HOME": "/tmp", "LANG": "C.UTF-8", "PYTHONHASHSEED": "0"}
        for k in self.profile.env_allowlist:
            if k in os.environ:
                env[k] = os.environ[k]
        return env

    def run_argv(self, ws: Workspace, name: str, harness: str = "probe") -> list[str]:
        p = self.profile
        for path in (ws.src, ws.harness):
            if "," in str(path) or "=" in str(path):
                raise RunnerError(f"workspace path {path} cannot be expressed as a --mount option")
        argv = ["run", "--rm", "-i", "--name", name, "--pull", "never",
                "--network", "none", "--read-only",
                "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={p.scratch_mb}m",
                "--mount", f"type=bind,source={ws.src},target={CONTAINER_SRC},readonly",
                "--mount", f"type=bind,source={ws.harness},target={CONTAINER_HARNESS},readonly",
                "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--pids-limit", str(p.max_processes), "--memory", f"{p.memory_mb}m", "--memory-swap", f"{p.memory_mb}m",
                "--cpus", f"{p.cpus:g}", "--ulimit", f"cpu={p.cpu_seconds}", "--ulimit", "core=0", "--ulimit", "nofile=256:256",
                "--ipc", "none", "--log-driver", "none", "--workdir", "/tmp", "--hostname", "ig-probe"]
        for k, v in sorted(self.container_env().items()):
            argv += ["--env", f"{k}={v}"]
        assert self.image_id is not None
        argv += ["--entrypoint", "python", self.image_id, *HARNESS_FLAGS, f"{CONTAINER_HARNESS}/{HARNESSES[harness][0]}"]
        return argv

    def _remove(self, name: str) -> None:
        self.cli(["rm", "-f", name], timeout_s=30)

    def container_gone(self, name: str) -> bool:
        r = self.cli(["ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.ID}}"], timeout_s=30)
        return r.returncode == 0 and not r.stdout.strip()

    def run(self, ws: Workspace, request: dict[str, Any], harness: str = "probe") -> HarnessRun:
        if self.exe is None or self.image_id is None:
            return HarnessRun(None, "docker backend not initialized", None)
        name = f"ig-probe-{uuid.uuid4().hex[:16]}"
        data = json.dumps(request, separators=(",", ":"), allow_nan=False).encode()
        if len(data) > self.profile.max_input_bytes:
            return HarnessRun(None, f"probe request of {len(data)} bytes exceeds max_input_bytes", None, name)
        self.runs += 1
        res = run_argv([self.exe, *self.run_argv(ws, name, harness)], stdin=data, env=self._env(), timeout_s=self.profile.wall_seconds,
                       max_stdout=self.profile.max_output_bytes, on_timeout=lambda: self._remove(name))
        resp, err = _parse_response(res, request["mode"], HARNESSES[harness][1])
        if res.timed_out or res.stdout_truncated:
            self._remove(name)
            if not self.container_gone(name):
                err = (err or "") + "; the container could not be confirmed removed (do not reuse this environment)"
        return HarnessRun(resp, err, res, name)

    def status(self) -> BackendStatus:
        if self._status is not None:
            return self._status
        st = BackendStatus("docker", False, "", True)
        st.capabilities = [Capability(c, True, "NOT_PROBED") for c in self.profile.required_capabilities]
        version, why = self.server_version()
        if version is None:
            st.reason = why
            self._status = st
            return st
        info, why = self.inspect_image()
        if info is None:
            st.reason = why
            st.environment = {"docker_server": version}
            self._status = st
            return st
        self.image_id = info["image_id"]
        self.image_env = [e.split("=", 1)[0] for e in info["env"]]
        st.environment = {"docker_server": version, "image": info["image"], "image_id": info["image_id"], "repo_digests": info["repo_digests"],
                          "os": info["os"], "architecture": info["architecture"], "harness_digest": harness_digest()}
        ws = Workspace({}, "capprobe")
        try:
            expect = {"memory_bytes": self.profile.memory_mb * 1024 * 1024, "pids": self.profile.max_processes,
                      "cpu_max": f"{int(round(self.profile.cpus * 100000))} 100000", "cpu_seconds": self.profile.cpu_seconds,
                      "python_minor": list(sys.version_info[:2])}
            allowed_env = set(self.image_env) | set(self.container_env()) | {"HOSTNAME"}
            run = self.run(ws, {"mode": "capabilities", "src_root": CONTAINER_SRC, "scratch": "/tmp", "expect": expect, "allowed_env": sorted(allowed_env)})
        finally:
            ws.close()
        if run.response is None:
            st.reason = f"capability probe failed: {run.error}"
            self._status = st
            return st
        caps = run.response.get("capabilities") or {}
        ident = run.response.get("identity") or {}
        st.interpreter_version = ident.get("version")
        st.environment["interpreter"] = ident
        st.capabilities = []
        failed = []
        for name in self.profile.required_capabilities:
            c = caps.get(name)
            ok = isinstance(c, dict) and c.get("verified") is True
            st.capabilities.append(Capability(name, True, "VERIFIED" if ok else "FAILED", c))
            if not ok:
                failed.append(name)
        interp = caps.get("interpreter") or {}
        st.capabilities.append(Capability("interpreter_minor_matches_host", True, "VERIFIED" if interp.get("verified") else "FAILED", interp))
        if not interp.get("verified"):
            failed.append("interpreter_minor_matches_host")
        ienv = caps.get("interpreter_env") or {}
        st.capabilities.append(Capability("deterministic_interpreter_env", True, "VERIFIED" if ienv.get("verified") else "FAILED", ienv))
        if not ienv.get("verified"):
            failed.append("deterministic_interpreter_env")
        st.environment_digest = digest_json({"backend": "docker", "image_id": info["image_id"], "interpreter": ident.get("version"),
                                             "harness": harness_digest()})
        if failed:
            st.reason = "required isolation capability check(s) failed inside the container: " + ", ".join(failed)
            self._status = st
            return st
        st.available = True
        st.reason = f"docker {version}; image {info['image']} ({info['image_id'][:19]}…); all required capabilities verified from inside the boundary"
        self._status = st
        return st

    def setup(self) -> dict[str, Any]:
        """Explicit environment preparation (``guard runner setup``): the only network use."""
        version, why = self.server_version()
        if version is None:
            raise RunnerError(why)
        ref = self.profile.image or ""
        if self.profile.image_digest:
            ref = ref.split("@", 1)[0] + "@" + self.profile.image_digest
        r = self.cli(["pull", "--", ref], timeout_s=900, max_stdout=4 << 20)
        if r.returncode != 0:
            raise RunnerError(f"docker pull {ref} failed: {r.stderr.decode('utf-8', 'replace').strip()[:400]}")
        info, why = self.inspect_image()
        if info is None:
            raise RunnerError(why)
        self.image_id = info["image_id"]
        ws = Workspace({}, "setup")
        try:
            run = self.run(ws, {"mode": "identity"})
        finally:
            ws.close()
        if run.response is None:
            raise RunnerError(f"identity check in the prepared image failed: {run.error}")
        ident = run.response["identity"]
        return {"docker_server": version, "image": info["image"], "image_id": info["image_id"], "repo_digests": info["repo_digests"],
                "architecture": info["architecture"], "interpreter": ident,
                "interpreter_minor_matches_host": list(ident.get("version_info", [])[:2]) == list(sys.version_info[:2]),
                "host_python": sys.version.split()[0]}


# -- unisolated -----------------------------------------------------------------------------------------
class UnisolatedBackend(Backend):
    name = "unisolated"
    isolated = False

    def src_root(self, ws: Workspace) -> str:
        return str(ws.src)

    def status(self) -> BackendStatus:
        if self._status is not None:
            return self._status
        detail = "explicit unisolated profile (execution.allow_unisolated = true): not enforced"
        caps = [Capability(c, False, "NOT_ENFORCED", detail) for c in ("deny_network", "readonly_source", "no_host_credentials", "scratch_only_writes")]
        caps.append(Capability("resource_limits", False, "NOT_ENFORCED", "partial only: per-probe RLIMIT_CPU and wall-clock kill; no memory, PID or filesystem limits"))
        ident = {"implementation": sys.implementation.name, "version": sys.version.split()[0], "executable": sys.executable}
        self._status = BackendStatus("unisolated", True, "UNISOLATED execution on the host (explicitly allowed by configuration); not a sandbox", False,
                                     caps, {"interpreter": ident, "harness_digest": harness_digest()},
                                     digest_json({"backend": "unisolated", "interpreter": ident, "harness": harness_digest()}), ident["version"])
        return self._status

    def run(self, ws: Workspace, request: dict[str, Any], harness: str = "probe") -> HarnessRun:
        data = json.dumps(request, separators=(",", ":"), allow_nan=False).encode()
        if len(data) > self.profile.max_input_bytes:
            return HarnessRun(None, f"probe request of {len(data)} bytes exceeds max_input_bytes", None)
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONHASHSEED": "0", "HOME": str(ws.scratch)}
        for k in self.profile.env_allowlist:
            if k in os.environ:
                env[k] = os.environ[k]
        self.runs += 1
        res = run_argv([sys.executable, *HARNESS_FLAGS, str(ws.harness / HARNESSES[harness][0])], stdin=data, env=env, cwd=str(ws.scratch),
                       timeout_s=self.profile.wall_seconds, max_stdout=self.profile.max_output_bytes)
        resp, err = _parse_response(res, request["mode"], HARNESSES[harness][1])
        return HarnessRun(resp, err, res)


def make_backend(profile: RunProfileConfig) -> Backend:
    return DockerBackend(profile) if profile.backend == "docker" else UnisolatedBackend(profile)


__all__ = ["Backend", "DockerBackend", "UnisolatedBackend", "BackendStatus", "Capability", "Workspace", "HarnessRun", "RunnerError", "make_backend",
           "harness_digest", "validate_observations", "find_docker", "HARNESSES"]
