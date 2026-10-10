"""The probe harness interpreter honors PYTHONHASHSEED (spikes/diffreplay/REPORT.md, gap 5).

The runner used ``python -I``, which implies ``-E``: PYTHONHASHSEED was ignored and str
hashing (set iteration order) was randomized per run inside the "deterministic" harness.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from invariantguard.execution.profiles import ProfileError, parse_profile
from invariantguard.execution.runner import HARNESS_FLAGS, harness_bytes


def _identity(tmp_path, flags, seed="0") -> dict:
    h = tmp_path / "probe_harness.py"
    h.write_bytes(harness_bytes())
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONHASHSEED": seed, "HOME": str(tmp_path)}
    out = subprocess.run([sys.executable, *flags, str(h)], input=json.dumps({"mode": "identity"}).encode(), env=env,
                         capture_output=True, cwd=tmp_path, timeout=60, check=True).stdout
    return json.loads(out)["identity"]


def test_harness_flags_honor_the_hash_seed(tmp_path):
    ident = _identity(tmp_path, HARNESS_FLAGS)
    assert ident["hash_seed"] == "0" and ident["hash_randomization"] == 0 and ident["ignore_environment"] is False
    assert ident["executable_flags"]["no_site"] is True


def test_isolated_mode_would_ignore_the_seed(tmp_path):
    """Documents why -I is not used."""
    ident = _identity(tmp_path, ("-I", "-S", "-B"))
    assert ident["hash_randomization"] == 1


def test_interpreter_variables_cannot_be_allowlisted():
    with pytest.raises(ProfileError, match="PYTHON"):
        parse_profile("x", {"backend": "docker", "image": "python:3.13-slim", "allowed_targets": ["a.py::*"], "env_allowlist": ["PYTHONPATH"]})
