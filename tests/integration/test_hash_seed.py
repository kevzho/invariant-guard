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


def test_check_output_does_not_depend_on_the_hash_seed(tmp_path):
    """The example effect named for a loop or ``try`` havoc is chosen in program order, not by
    set iteration (str hashing), so identical runs give identical obligation results."""
    from ..conftest import run_guard_subprocess

    (tmp_path / ".git").mkdir()
    (tmp_path / "m.py").write_text(
        "def f(xs: list[int], d: int):\n"
        "    for x in xs:\n"
        "        if x:\n            print(x)\n        elif x > 2:\n            xs.sort()\n        else:\n            len(str(x))\n"
        "    try:\n        a = 1\n        open('p')\n        sorted(xs)\n        repr(xs)\n    except OSError:\n        a = 2\n"
        "    return 10 // d\n", encoding="utf-8")
    outs = set()
    for seed in ("1", "2", "3", "4", "5"):
        p = run_guard_subprocess(["check", str(tmp_path), "--format", "json"], cwd=tmp_path, env={"PYTHONHASHSEED": seed})
        rep = json.loads(p.stdout)
        outs.add(json.dumps([{k: o[k] for k in ("obligation_id", "property_result", "assumptions", "reasons")} for o in rep["obligation_results"]], sort_keys=True))
    assert len(outs) == 1
