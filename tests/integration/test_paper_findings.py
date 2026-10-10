"""Defects found by the paper benchmark (benchmarks/bench_seeded.py).

U02 (quick mode said PASS for a guard removed inside an unsupported nested function) was a
strict xfail against the frozen engine the paper evaluated; it is fixed on the phase-1 branch
(unsupported-symbol fingerprints now cover the whole definition) and is a regular test here.
"""

from __future__ import annotations

from .gitrepo import config, make_repo, verify, write

BASE = """def outer(a: int, b: int):
    def inner():
        if a == b:
            return 0.0
        return 10 / (a - b)
    return inner()
"""
PATCH = """def outer(a: int, b: int):
    def inner():
        return 10 / (a - b)
    return inner()
"""


def test_full_mode_reports_the_changed_nested_function_as_incomplete(tmp_path):
    root, base = make_repo(tmp_path, {"app/nested.py": BASE}, config("app/nested.py::outer"))
    write(root, {"app/nested.py": PATCH})
    code, r, _ = verify(root, base, "--mode", "full", "--no-cache")
    assert r["run_outcome"] == "INCOMPLETE" and r["verdict"] == "INCOMPLETE"
    assert any(u["symbol_id"] == "app/nested.py::outer.<locals>.inner" and u["required"] for u in r["coverage"]["unknown_obligations"])


def test_quick_mode_does_not_pass_a_changed_nested_function(tmp_path):
    root, base = make_repo(tmp_path, {"app/nested.py": BASE}, config("app/nested.py::outer"))
    write(root, {"app/nested.py": PATCH})
    code, r, _ = verify(root, base, "--mode", "quick", "--no-cache")
    assert r["verdict"] != "PASS"
