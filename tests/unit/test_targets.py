"""Static resolution of contract targets (AST only): functions, static and class methods, clear rejections."""

import pytest

from invariantguard.contracts.targets import TargetError, function_node, resolve_target

SNAP = "sha256:" + "0" * 64
SRC = b'''
def plain(a, b=1, *, c):
    return a


class Ops:
    """Holder."""

    @staticmethod
    def add(a, b):
        return a + b

    @classmethod
    def make(cls, n, /, *, scale=1):
        return [n] * scale

    def inst(self, x):
        return x

    @property
    def prop(self):
        return 1

    @staticmethod
    @classmethod
    def double(cls, x):
        return x

    @classmethod
    def nocls(*, x):
        return x

    async def coro(self, x):
        return x

    @staticmethod
    def twice(x):
        return x

    twice = staticmethod(lambda x: x)

    class Inner:
        @staticmethod
        def f(x):
            return x


@decorate
class Deco:
    @staticmethod
    def f(x):
        return x


class Meta(metaclass=type):
    @staticmethod
    def f(x):
        return x


def notaclass():
    pass
'''
REBOUND = b"staticmethod = lambda f: f\n\n\nclass R:\n    @staticmethod\n    def f(x):\n        return x\n"
FILES = {"pkg/mod.py": SRC, "pkg/rebound.py": REBOUND}


def test_module_function_target_is_unchanged():
    t = resolve_target("pkg/mod.py::plain", FILES, SNAP)
    assert (t.function, t.owner, t.binding, t.positional, t.keyword_only) == ("plain", None, "function", ("a", "b"), ("c",))
    assert "owner" not in t.call_spec() and "class" not in t.to_json()


def test_static_method_target():
    t = resolve_target("pkg/mod.py::Ops.add", FILES, SNAP)
    assert (t.module, t.function, t.owner, t.binding, t.params) == ("pkg.mod", "add", "Ops", "staticmethod", ("a", "b"))
    assert t.call_spec() == {"module": "pkg.mod", "function": "add", "positional": ["a", "b"], "keyword_only": [], "python_path_root": ".",
                             "owner": "Ops", "binding": "staticmethod"}
    assert t.def_span.to_json()["start_line"] == 10  # the def line (decorators are not the header)
    assert function_node(FILES, "pkg/mod.py::Ops.add").name == "add"


def test_class_method_target_excludes_cls():
    t = resolve_target("pkg/mod.py::Ops.make", FILES, SNAP)
    assert (t.binding, t.positional, t.keyword_only) == ("classmethod", ("n",), ("scale",))
    assert t.to_json()["class"] == "Ops" and t.to_json()["binding"] == "classmethod"


@pytest.mark.parametrize("scope,code,fragment", [
    ("pkg/mod.py::Ops.inst", "UNSUPPORTED_TARGET", "instance methods are unsupported"),
    ("pkg/mod.py::Ops.prop", "UNSUPPORTED_TARGET", "exactly @staticmethod or @classmethod"),
    ("pkg/mod.py::Ops.double", "UNSUPPORTED_TARGET", "exactly @staticmethod or @classmethod"),
    ("pkg/mod.py::Ops.nocls", "UNSUPPORTED_TARGET", "without a positional 'cls'"),
    ("pkg/mod.py::Ops.coro", "UNSUPPORTED_TARGET", "not a plain (non-async) def"),
    ("pkg/mod.py::Ops.twice", "UNSUPPORTED_TARGET", "bound more than once"),
    ("pkg/mod.py::Ops.Inner.f", "UNSUPPORTED_TARGET", "nested classes"),
    ("pkg/mod.py::Ops.Inner", "UNSUPPORTED_TARGET", "not a plain"),
    ("pkg/mod.py::Ops.missing", "UNKNOWN_SYMBOL", "class Ops body"),
    ("pkg/mod.py::Deco.f", "UNSUPPORTED_TARGET", "class Deco is decorated"),
    ("pkg/mod.py::Meta.f", "UNSUPPORTED_TARGET", "metaclass"),
    ("pkg/mod.py::notaclass.f", "UNSUPPORTED_TARGET", "not a module-level class"),
    ("pkg/mod.py::Nope.f", "UNKNOWN_SYMBOL", "module level"),
    ("pkg/mod.py::Ops", "UNSUPPORTED_TARGET", "not a plain"),
    ("pkg/mod.py::Ops.", "UNSUPPORTED_TARGET", "identifiers only"),
    ("pkg/mod.py::Ops.add()", "UNSUPPORTED_TARGET", "identifiers only"),
    ("pkg/rebound.py::R.f", "UNSUPPORTED_TARGET", "rebound"),
])
def test_unsupported_method_targets_are_rejected_clearly(scope, code, fragment):
    with pytest.raises(TargetError) as exc:
        resolve_target(scope, FILES, SNAP)
    assert exc.value.code == code and fragment in exc.value.message, exc.value.message
