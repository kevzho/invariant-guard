"""Audit (M2/M3): extra incremental-vs-full change cases.

Each case changes only *module-level facts* a dependent relies on (a `global` declaration
elsewhere in the callee's module, a dynamic-globals use, a decorator, a rebinding, a
default argument, an import alias, a package that shadows a module, a star import), never
the dependent's own text. `assert_consistent` (test_incremental.py) checks that the quick
slice equals a cold full run, that warm equals cold, and that everything outside the slice
equals an independent check of the base tree.
"""

from __future__ import annotations

import pytest

from .gitrepo import config, write
from .test_incremental import DETERMINISTIC, assert_consistent

LIB = {
    "util.py": "SCALE = 3\n\n\ndef denom(x, k=1):\n    return x + k\n\n\ndef shift(x):\n    return x - SCALE\n",
    "app.py": ("from util import denom as dn, shift\n\n\ndef ratio(x):\n    if x < 0:\n        return 0\n    return 100 // dn(x)\n\n\n"
               "def scaled(x):\n    d = shift(x)\n    if d != 0:\n        return 1 / d\n    return 7 // d\n"),
}
DOMAINS = config("util.py::denom", "util.py::shift", "app.py::ratio", "app.py::scaled", extra=DETERMINISTIC)
U = LIB["util.py"]

CASES = {
    "global-decl-elsewhere": {"util.py": U + "\n\ndef bump():\n    global SCALE\n    SCALE = 0\n"},
    "dynamic-globals-use": {"util.py": U + "\n\ndef peek():\n    return globals()\n"},
    "decorate-callee": {"util.py": "import functools\n" + U.replace("def denom(x, k=1):", "@functools.lru_cache\ndef denom(x, k=1):")},
    "rebind-callee-name": {"util.py": U + "\n\ndenom = abs\n"},
    "default-arg-change": {"util.py": U.replace("k=1", "k=0")},
    "star-import": {"util.py": "from os.path import *\n" + U},
    "package-shadows-module": {"util/__init__.py": "def denom(x, k=1):\n    return x - x\n"},
    "constant-to-zero": {"util.py": U.replace("SCALE = 3", "SCALE = 0")},
    "conditional-rebinding": {"util.py": U + "\nif SCALE > 2:\n    SCALE = 0\n"},
}


@pytest.mark.parametrize("case", sorted(CASES), ids=sorted(CASES))
def test_audit_change_case_incremental_equals_full(tmp_path, case):
    out = assert_consistent(tmp_path, LIB, DOMAINS, lambda r: write(r, CASES[case]))
    inv = {i["symbol_id"] for i in out["quick"]["patch"]["slice"]["invalidated"]}
    # Every case changes a fact that app.py relies on without touching app.py.
    assert inv & {"app.py::ratio", "app.py::scaled"}, (case, inv)
