"""Quick verify lowers only the files a change can reach (I-143), with the same results as full.

Each case mutates a project whose dependencies run through the import forms the resolver
follows: package re-exports, relative imports, aliases, function-local imports, methods and
chains through a module constant. ``assert_consistent`` (test_incremental) checks that the
scoped quick run equals a full run (which lowers everything) on the slice, and that every
obligation outside the slice equals an independent check of the base tree. On top of that,
the expected dependents must be lowered and the unrelated file must not be.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .gitrepo import config, write
from .test_incremental import DETERMINISTIC, assert_consistent

PROJECT = {
    "pkg/__init__.py": "from .core import denom as d\n",
    "pkg/consts.py": "K = 1\n",
    "pkg/core.py": "from .consts import K\n\n\ndef denom(x):\n    return x + K\n",
    "app.py": ("from pkg import d\n\n\ndef helper(x):\n    return d(x)\n\n\n"
               "def ratio(x: int):\n    if x < 0:\n        return 0\n    return 100 // helper(x)\n\n\n"
               "def other(x: int):\n    return 7 // (x * x + 1)\n"),
    "local.py": "def lazy(x: int):\n    from pkg.core import denom\n    if x < 0:\n        return 0\n    return 10 // denom(x)\n",
    "meth.py": ("from pkg import d\n\n\nclass C:\n    def m(self, x: int):\n        if x < 0:\n            return 0\n"
                "        return 10 // d(x)\n"),
    "unrelated.py": "def u(x: int):\n    if x == 0:\n        return 0\n    return 1 // x\n",
    # A class whose method reaches the change is lowered; a file that only names the class is not
    # (the resolver resolves a class name to a descriptor of the class's own AST).
    "cls.py": "from pkg import d\n\n\nclass Box:\n    def get(self, x: int):\n        if x < 0:\n            return 0\n        return 10 // d(x)\n",
    "uses_cls.py": "from cls import Box\n\n\ndef mk(x: int):\n    b = Box()\n    return 5 // (x * x + 1)\n",
}
CFG = config("app.py::ratio", "local.py::lazy", "unrelated.py::u", extra=DETERMINISTIC)

CASES = {
    # K = 0 makes denom(0) == 0: reached through core -> __init__ alias -> app.helper -> ratio.
    "constant-two-hops": (lambda r: write(r, {"pkg/consts.py": "K = 0\n"}), {"app.py", "local.py", "meth.py", "pkg/core.py"}),
    "callee-body": (lambda r: write(r, {"pkg/core.py": PROJECT["pkg/core.py"].replace("x + K", "x - K")}), {"app.py", "local.py", "meth.py", "cls.py"}),
    "re-export-target": (lambda r: write(r, {"pkg/__init__.py": "from .core import denom as d\n\n\ndef denom2(x):\n    return x\n"}),
                         {"app.py", "meth.py"}),
    # A package directory next to pkg/core.py makes `pkg.core` ambiguous for every importer.
    "ambiguous-module": (lambda r: write(r, {"pkg/core/__init__.py": "def denom(x):\n    return 0\n"}), {"app.py", "local.py", "meth.py"}),
    "unrelated-edit": (lambda r: write(r, {"unrelated.py": PROJECT["unrelated.py"].replace("x == 0", "x == 1")}), set()),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_scoped_quick_equals_full(tmp_path: Path, case: str):
    mutate, must_lower = CASES[case]
    out = assert_consistent(tmp_path, PROJECT, CFG, mutate)
    low = out["quick"]["patch"]["lowering"]
    assert low["scoped"] is True and out["full"]["patch"]["lowering"]["scoped"] is False
    lowered = {x["path"] for x in low["lowered"]}
    assert must_lower <= lowered, (must_lower - lowered, low["lowered"])
    assert "uses_cls.py" not in lowered
    if case != "unrelated-edit":
        assert "unrelated.py" not in lowered
    app = next((x for x in low["lowered"] if x["path"] == "app.py"), None)
    if app is not None:  # app.py is unchanged in every case: only the statements that reach the change
        assert app["statements"] == 2, app  # helper and ratio, not the import or other
    assert low["files_lowered"] + low["files_not_lowered"] == len(PROJECT) + (case == "ambiguous-module")


def test_unchanged_caller_failure_found_through_reexport(tmp_path: Path):
    """The cross-file case docs/07 names: an unchanged caller becomes unsafe when the callee changes."""
    out = assert_consistent(tmp_path, PROJECT, CFG, CASES["constant-two-hops"][0])
    got = {(o["symbol_id"], o["operation_text"]): o["property_result"] for o in out["quick"]["obligation_results"]}
    assert got[("app.py::ratio", "100 // helper(x)")] == "PROVED_UNSAFE"
    assert got[("meth.py::C.m", "10 // d(x)")] == "PROVED_UNSAFE"
    # A function-local import is a call of a local variable (unknown result): lazy cannot
    # depend on denom's body, so it is outside the slice (the scope still lowers local.py).
    assert ("local.py::lazy", "10 // denom(x)") not in got
    full = {(o["symbol_id"], o["operation_text"]): o["property_result"] for o in out["full"]["obligation_results"]}
    assert full[("local.py::lazy", "10 // denom(x)")] == "UNKNOWN"
