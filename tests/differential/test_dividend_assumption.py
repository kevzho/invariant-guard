"""Division by a nonzero built-in number with a dividend of unmodeled type (I-147).

Built-in division, str formatting and the divisor's own reflected method cannot raise
ZeroDivisionError for a nonzero divisor, so only a non-built-in dividend's operator method
could. That is listed as an assumption (CONDITIONAL), it is configurable, and CPython shows
why it is needed: a user ``__mod__`` can raise ZeroDivisionError for ``% 7``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..conftest import check_json, write_project

SRC = '''def lit(x):
    return x % 7


def flt(x):
    return x / 2.5


def neg(x):
    return x // -3


def guarded(x, n: int):
    if n > 0:
        return x // n
    return 0


def unguarded(x, n: int):
    return x // n


def zero_float(x):
    return x / 0.0


def unknown_divisor(x, n):
    return x % n
'''
EVIL = '''class Evil:
    def __mod__(self, other):
        return 1 // 0
'''
ASSUMPTION = "if the dividend is not a built-in int, float or str"


def results(root: Path) -> dict[str, dict]:
    _, rep, _ = check_json(root)
    return {o["symbol_id"].split("::")[1]: o for o in rep["obligation_results"]}


def test_nonzero_divisor_with_unmodeled_dividend(tmp_path: Path):
    root = write_project(tmp_path / "proj", {"m.py": SRC})
    (root / ".git").mkdir()
    got = results(root)
    for fn in ("lit", "flt", "neg", "guarded"):
        o = got[fn]
        assert (o["property_result"], o["scope_validity"]) == ("PROVED_SAFE", "CONDITIONAL"), (fn, o["reasons"])
        assert any(a.startswith(ASSUMPTION) for a in o["assumptions"]), fn
    for fn in ("unguarded", "zero_float", "unknown_divisor"):
        assert got[fn]["property_result"] != "PROVED_SAFE", (fn, got[fn]["reasons"])
        assert not any(a.startswith(ASSUMPTION) for a in got[fn]["assumptions"]), fn


def test_assumption_is_configurable(tmp_path: Path):
    root = write_project(tmp_path / "proj", {"m.py": SRC}, config='schema_version = "1.0"\n[analysis]\nassume_dividend_operator_respects_nonzero = false\n')
    (root / ".git").mkdir()
    got = results(root)
    for fn in ("lit", "flt", "neg", "guarded"):
        assert got[fn]["property_result"] == "UNKNOWN", fn
        assert got[fn]["reasons"][0].startswith("dividend has type"), fn


def test_cpython_shows_the_assumption_is_needed(tmp_path: Path):
    """A user __mod__ can raise ZeroDivisionError for `x % 7`: the proof is conditional, not exact."""
    (tmp_path / "m.py").write_text(SRC + "\n\n" + EVIL, encoding="utf-8")
    probe = ("import runpy, sys\nns = runpy.run_path(sys.argv[1])\ntry:\n    ns['lit'](ns['Evil']())\n    print('none')\n"
             "except ZeroDivisionError:\n    print('ZeroDivisionError')\nprint(ns['lit'](10), ns['lit'](10.5), ns['lit']('%d'))\n")
    out = subprocess.run([sys.executable, "-I", "-c", probe, str(tmp_path / "m.py")], capture_output=True, text=True, timeout=60).stdout.split("\n")
    assert out[0] == "ZeroDivisionError"
    assert out[1] == "3 3.5 7"  # built-in int, float and str % 7 never raise ZeroDivisionError
