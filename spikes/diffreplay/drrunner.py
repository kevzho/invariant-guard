"""Host side of the spike: reuse invariantguard's DockerBackend / Workspace / process limits.

What is reused unchanged: DockerBackend capability probing (status), the container flags
(network none, read-only root and source, tmpfs scratch, uid 65534, cap-drop ALL, cgroup
limits), ``run_argv`` process control, Workspace materialization and path checks.

What had to be worked around (gaps, see REPORT.md):
* the harness file name and protocol are hard-coded (``probe_harness.py``, ``ig-probe-1``);
  we subclass ``run_argv`` to point at our harness placed in the same read-only mount;
* ``PYTHONHASHSEED`` is fixed at 0 in ``container_env``; we vary it for the second base run
  so hash-order nondeterminism becomes visible;
* the harness is started with ``-S`` so the image's site-packages (pinned deps such as
  ``six`` for dateutil, and hypothesis) is not importable; our harness appends it explicitly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from invariantguard.execution.profiles import RunProfileConfig
from invariantguard.execution.runner import CONTAINER_HARNESS, DockerBackend, HarnessRun, Workspace

HERE = Path(__file__).resolve().parent
DR_HARNESS = "dr_harness.py"
IMAGE = "invariantguard-diffreplay:spike"


def make_profile() -> RunProfileConfig:
    return RunProfileConfig(name="diffreplay-spike", backend="docker", allowed_targets=["*::*"], image=IMAGE,
                            cpu_seconds=600, memory_mb=1024, wall_seconds=900, probe_wall_seconds=2.0, max_processes=64, scratch_mb=64,
                            max_input_bytes=16 << 20, max_output_bytes=64 << 20, max_result_bytes=1 << 20)


class DRBackend(DockerBackend):
    def __init__(self, profile: RunProfileConfig, hash_seed: str = "0", harness: str = DR_HARNESS) -> None:
        super().__init__(profile)
        self.hash_seed = hash_seed
        self.harness_name = harness

    def container_env(self) -> dict[str, str]:
        env = super().container_env()
        env["PYTHONHASHSEED"] = self.hash_seed
        return env

    def run_argv(self, ws: Workspace, name: str) -> list[str]:
        argv = super().run_argv(ws, name)
        assert argv[-1].endswith("/probe_harness.py")
        if (ws.harness / self.harness_name).exists():  # capability probes keep using the trusted probe harness
            argv[-1] = f"{CONTAINER_HARNESS}/{self.harness_name}"
        return argv


def workspace(files: dict[str, bytes], label: str) -> Workspace:
    ws = Workspace(files, label)
    for name in (DR_HARNESS, "dr_pytest.py"):
        hp = ws.harness / name
        hp.write_bytes((HERE / name).read_bytes())
        os.chmod(hp, 0o644)
    return ws


_BACKENDS: dict[str, DRBackend] = {}


def backend(hash_seed: str = "0", harness: str = DR_HARNESS) -> DRBackend:
    b = _BACKENDS.get(hash_seed + harness)
    if b is None:
        b = DRBackend(make_profile(), hash_seed, harness)
        st = b.status()
        if not st.available:
            raise SystemExit(f"runner unavailable: {st.reason}")
        _BACKENDS[hash_seed + harness] = b
    return b


def run(ws: Workspace, req: dict[str, Any], hash_seed: str = "0", harness: str = DR_HARNESS) -> HarnessRun:
    b = backend(hash_seed, harness)
    req = {**req, "src_root": b.src_root(ws)}
    return b.run(ws, req)


def environment() -> dict[str, Any]:
    st = backend("0").status()
    return {"backend": st.to_json(), "host_python": sys.version.split()[0]}
