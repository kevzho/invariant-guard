"""Phase 1: change detection covers whole definitions, doc-only edits and data dependencies.

The differential-replay spike (spikes/diffreplay/REPORT.md, gap 8) found that 81 of 134
changed functions in real commits were invisible to ``match_symbols``: unsupported symbols
(methods, generators, decorated or nested functions) were fingerprinted by their division
nodes only. These tests pin the fix through the public ``guard verify`` report.
"""

from __future__ import annotations

from .gitrepo import make_repo, verify, write


def _patch(tmp_path, base: dict[str, str], patch: dict[str, str]) -> dict:
    root, rev = make_repo(tmp_path, base)
    write(root, patch)
    code, r, err = verify(root, rev, "--mode", "quick", "--no-cache")
    assert r["errors"] == [], r["errors"]
    return r["patch"]


def _invalidated(p: dict) -> dict[str, str]:
    return {i["symbol_id"]: i["reason"] for i in p["slice"]["invalidated"]}


def test_method_body_change_is_a_modification(tmp_path):
    base = "class Box:\n    def get(self, i):\n        return self.items[i]\n"
    p = _patch(tmp_path, {"m.py": base}, {"m.py": base.replace("self.items[i]", "self.items[i - 1]")})
    assert p["symbols"]["modified"] == ["m.py::Box.get"]
    assert p["symbols"]["doc_only"] == []


def test_generator_and_decorated_function_body_changes_are_modifications(tmp_path):
    base = ("import functools\n\n"
            "def chunks(xs, n):\n    for i in range(0, len(xs), n):\n        yield xs[i:i + n]\n\n"
            "@functools.cache\ndef fib(n):\n    return n if n < 2 else fib(n - 1) + fib(n - 2)\n")
    patch = base.replace("range(0, len(xs), n)", "range(0, len(xs) - 1, n)").replace("n < 2", "n <= 2")
    p = _patch(tmp_path, {"m.py": base}, {"m.py": patch})
    assert set(p["symbols"]["modified"]) == {"m.py::chunks", "m.py::fib"}


def test_docstring_only_edit_is_flagged_doc_only(tmp_path):
    base = 'def f(xs):\n    """Return xs.\n\n    >>> f([1])\n    [1]\n    """\n    yield from xs\n'
    p = _patch(tmp_path, {"m.py": base}, {"m.py": base.replace("Return xs.", "Yield every item of xs.")})
    assert p["symbols"]["modified"] == ["m.py::f"]
    assert p["symbols"]["doc_only"] == ["m.py::f"]


def test_code_and_doc_edit_is_not_doc_only(tmp_path):
    base = 'def f(xs):\n    """Return xs."""\n    yield from xs\n'
    patch = base.replace("Return xs.", "Return reversed xs.").replace("yield from xs", "yield from reversed(xs)")
    p = _patch(tmp_path, {"m.py": base}, {"m.py": patch})
    assert p["symbols"]["modified"] == ["m.py::f"] and p["symbols"]["doc_only"] == []


def test_module_data_change_reaches_unsupported_readers(tmp_path):
    # bytes2human-style: behavior changes only through a module-level table.
    base = ("SYMBOLS = {'K': 1024, 'M': 1024 ** 2}\n\n"
            "def human(n):\n    for k, v in sorted(SYMBOLS.items(), key=lambda kv: -kv[1]):\n        if n >= v:\n            return f'{n // v}{k}'\n    return str(n)\n\n"
            "def unrelated(xs):\n    yield from xs\n")
    patch = base.replace("'M': 1024 ** 2}", "'M': 1024 ** 2, 'G': 1024 ** 3}")
    p = _patch(tmp_path, {"m.py": base}, {"m.py": patch})
    inv = _invalidated(p)
    assert "m.py::human" in inv and "SYMBOLS" in inv["m.py::human"]
    assert "m.py::unrelated" not in inv
    assert p["symbols"]["modified"] == []


def test_imported_module_data_change_reaches_unsupported_readers(tmp_path):
    base = {"pkg/__init__.py": "", "pkg/table.py": "UNITS = ('a', 'b')\n",
            "pkg/use.py": "from pkg.table import UNITS\n\ndef pick(i):\n    for u in UNITS:\n        yield u * i\n"}
    p = _patch(tmp_path, base, {"pkg/table.py": "UNITS = ('a', 'b', 'c')\n"})
    assert "pkg/use.py::pick" in _invalidated(p)


def test_class_attribute_change_reaches_methods(tmp_path):
    base = ("class Fmt:\n    SEP = ','\n\n    def join(self, xs):\n        return self.SEP.join(xs)\n\n"
            "class Other:\n    def run(self):\n        return 1\n")
    p = _patch(tmp_path, {"m.py": base}, {"m.py": base.replace("SEP = ','", "SEP = ';'")})
    inv = _invalidated(p)
    assert "m.py::Fmt.join" in inv
    assert "m.py::Other.run" not in inv
    assert p["symbols"]["modified"] == []


def test_editing_one_method_does_not_modify_its_siblings(tmp_path):
    base = "class C:\n    def a(self):\n        return 1\n\n    def b(self):\n        return 2\n"
    p = _patch(tmp_path, {"m.py": base}, {"m.py": base.replace("return 2", "return 3")})
    assert p["symbols"]["modified"] == ["m.py::C.b"]
    assert "m.py::C.a" not in _invalidated(p)
