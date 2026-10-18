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


P01_BASE = "def inverse_sum(a: int, b: int):\n    return 1 / (a + b)\n"
P01_PATCH = "def inverse_sum(a: int, b: int):\n    s = a + b\n    return 1.0 / s\n"


def test_failure_moved_into_a_local_is_pre_existing(tmp_path):
    """P01: the patch only moves the denominator into a local; the failure is PRE_EXISTING, not unmatched."""
    root, base = make_repo(tmp_path, {"app/inv.py": P01_BASE}, config("app/inv.py::inverse_sum"))
    write(root, {"app/inv.py": P01_PATCH})
    for mode in ("quick", "full"):
        code, r, _ = verify(root, base, "--mode", mode, "--no-cache")
        rels = [(f["property_result"], f["patch_relation"]) for f in r["findings"] if f["symbol_id"] == "app/inv.py::inverse_sum"]
        assert ("PROVED_UNSAFE", "PRE_EXISTING") in rels, (mode, rels)
        assert not any(rel in ("INTRODUCED", "UNDETERMINED") for _, rel in rels), (mode, rels)
        assert r["verdict"] != "BLOCK" and r["verdict"] != "INCOMPLETE", (mode, r["verdict"], r.get("verdict_reasons"))


def test_moved_failure_with_a_kept_operation_is_not_matched_to_it(tmp_path):
    """A new failing operation is not PRE_EXISTING just because an unchanged operation fails in the base."""
    base_src = "def f(a: int, b: int):\n    x = 1 // a\n    return x\n"
    patch_src = "def f(a: int, b: int):\n    x = 1 // a\n    return x + 1 // (b - b)\n"
    root, base = make_repo(tmp_path, {"app/m.py": base_src}, config("app/m.py::f"))
    write(root, {"app/m.py": patch_src})
    code, r, _ = verify(root, base, "--mode", "full", "--no-cache")
    rels = [f["patch_relation"]
            for f in r["findings"] if f["symbol_id"] == "app/m.py::f" and f["property_result"] == "PROVED_UNSAFE"]
    assert "INTRODUCED" in rels, rels


def test_a_new_operation_beside_a_moved_one_is_not_paired(tmp_path):
    """Two unmatched candidate operations: no one-to-one move, so the new one is not PRE_EXISTING by pairing."""
    patch_src = "def inverse_sum(a: int, b: int):\n    s = a + b\n    return 1.0 / s + 1 // (a + b)\n"
    root, base = make_repo(tmp_path, {"app/inv.py": P01_BASE}, config("app/inv.py::inverse_sum"))
    write(root, {"app/inv.py": patch_src})
    code, r, _ = verify(root, base, "--mode", "full", "--no-cache")
    for f in r["findings"]:
        if f["symbol_id"] == "app/inv.py::inverse_sum" and f.get("comparison"):
            assert f["comparison"].get("witness_in_base") != "FAILED_MOVED_SITE", f["comparison"]
