"""Phase 3: harvesting replay inputs from tests and doctests (AST only), then replaying them."""

from __future__ import annotations

import pytest

from invariantguard.execution.profiles import parse_profile
from invariantguard.execution.runner import UnisolatedBackend
from invariantguard.replay.harvest import default_variants, harvest, target_kind
from invariantguard.replay.run import Expr, Target, Value, run_replay
from invariantguard.replay.values import decode_input

MOD = '''"""Toy module.

>>> scale([1, 2], 3)
[3, 6]
"""
import itertools

UNITS = ("a", "b")


def scale(xs, k=2, *, clamp=None):
    """Multiply.

    >>> ys = [4, 5]
    >>> scale(ys, k=10)
    [40, 50]
    """
    out = [x * k for x in xs]
    return out if clamp is None else [min(v, clamp) for v in out]


def pairs(it):
    return list(itertools.pairwise(it))


class Stack:
    """A stack.

    >>> Stack([1]).push(2)
    2
    """

    def __init__(self, items=()):
        self.items = list(items)

    def push(self, x):
        self.items.append(x)
        return len(self.items)

    def pop(self):
        return self.items.pop()

    @property
    def top(self):
        return self.items[-1]

    @staticmethod
    def of(*xs):
        return Stack(xs)

    def __getitem__(self, i):
        return self.items[i]

    def __len__(self):
        return len(self.items)


S = Stack


class Bounded(Stack):
    def push(self, x):
        return super().push(x) if len(self.items) < 2 else None


class Named(Stack):
    pass
'''

TESTS = '''import itertools
import pytest
from pkg.mod import Stack, S, Bounded, Named, scale, pairs
from pkg import mod


def helper():
    return 1


@pytest.mark.parametrize("xs, k", [([1], 2), ((1, 2), -1), pytest.param([0], 0, id="zero")])
def test_scale(xs, k):
    assert scale(xs, k) == [x * k for x in xs]


def test_local_and_helper():
    data = [1, 2, 3]
    more = data + [4]
    assert scale(more, k=3)
    assert scale(helper(), 2)          # helper is not harvestable
    assert mod.scale(range(3))


def test_pairs():
    assert pairs(itertools.count(1)) is not None or True
    assert pairs(iter("abc"))


def test_stack_sequence():
    s = Stack([1])
    s.push(2)
    s.push(3)
    assert s.pop() == 3
    assert s.top == 2
    assert s[0] == 1
    assert len(s) == 2


def test_alias_and_static():
    assert S([5]).push(6) == 2
    assert Stack.of(1, 2).top == 2


def test_subclasses():
    assert Named([1]).pop() == 1
    assert Bounded([1]).pop() == 1
    assert Bounded([1]).push(2) == 2


def test_rebinding():
    s = Stack()
    s = object()
    s.pop()                             # s is no longer a Stack: not harvested
'''

FILES = {"pkg/__init__.py": b"", "pkg/mod.py": MOD.encode(), "tests/test_mod.py": TESTS.encode()}
T = lambda q: Target("pkg/mod.py", q)  # noqa: E731


def _texts(res):
    return sorted(h.text for h in res.inputs)


def test_target_kinds():
    import ast

    tree = ast.parse(MOD)
    assert [target_kind(tree, q) for q in ("scale", "Stack", "Stack.push", "Stack.top", "Stack.of", "Stack.__getitem__", "nope")] == \
        ["function", "class", "method", "property", "staticmethod", "method", None]


def test_function_calls_from_parametrize_locals_and_doctests():
    res = harvest(T("scale"), FILES, ["tests/test_mod.py"])
    texts = _texts(res)
    assert texts.count("scale(xs, k)") == 3  # one per parametrize row
    assert "scale(more, k=3)" in texts and "mod.scale(range(3))" in texts
    assert "scale([1, 2], 3)" in texts and "scale(ys, k=10)" in texts  # module and function doctests
    assert not any("helper" in t for t in texts) and res.stats["unharvestable_args"] >= 1
    more = next(h for h in res.inputs if h.text == "scale(more, k=3)")
    (arg,) = more.args
    assert isinstance(arg, Expr) and arg.text == "more" and [b.name for b in arg.bindings] == ["data", "more"]
    doc = next(h for h in res.inputs if h.text == "scale(ys, k=10)")
    assert doc.origin == "doctest" and doc.args[0].bindings[0].text == "[4, 5]"
    assert all(isinstance(h.args[0], (Value, Expr)) for h in res.inputs)


def test_method_calls_get_receivers_and_preceding_steps():
    res = harvest(T("Stack.pop"), FILES, ["tests/test_mod.py"])
    (pop,) = [h for h in res.inputs if h.text == "s.pop()"]
    assert pop.receiver.text == "s" and pop.receiver.bindings[0].text == "Stack([1])"
    assert [(s.method, [a.node for a in s.args]) for s in pop.steps] == [("push", [{"t": "int", "v": "2"}]), ("push", [{"t": "int", "v": "3"}])]
    assert "rebinding" not in str(res.inputs)  # `s = object()` drops the Stack binding


def test_properties_dunders_aliases_and_static_methods():
    top = harvest(T("Stack.top"), FILES, ["tests/test_mod.py"])
    assert sorted(h.text for h in top.inputs) == ["Stack.of(1, 2).top", "s.top"]
    assert len(next(h for h in top.inputs if h.text == "s.top").steps) == 3  # push, push, pop
    getitem = harvest(T("Stack.__getitem__"), FILES, ["tests/test_mod.py"])
    assert _texts(getitem) == ["s[0]"]
    ln = harvest(T("Stack.__len__"), FILES, ["tests/test_mod.py"])
    assert _texts(ln) == ["len(s)"]
    push = harvest(T("Stack.push"), FILES, ["tests/test_mod.py"])
    assert "S([5]).push(6)" in _texts(push) and "Stack([1]).push(2)" in _texts(push)
    of = harvest(T("Stack.of"), FILES, ["tests/test_mod.py"])
    assert "Stack.of(1, 2)" in _texts(of)


def test_inheriting_subclass_receivers_only():
    pop = harvest(T("Stack.pop"), FILES, ["tests/test_mod.py"])
    assert {"Named([1]).pop()", "Bounded([1]).pop()"} <= set(_texts(pop))
    push = harvest(T("Stack.push"), FILES, ["tests/test_mod.py"])
    assert "Bounded([1]).push(2)" not in _texts(push)  # Bounded overrides push: not the target's code


def test_default_variants_perturb_omitted_defaults():
    res = harvest(T("scale"), FILES, ["tests/test_mod.py"])
    base = [h for h in res.inputs if h.text == "scale([1, 2], 3)"]
    var = default_variants(FILES, T("scale"), base)
    assert {k for h in var for k, _ in h.kwargs} == {"clamp"}  # k was given positionally
    assert all(h.origin == "defaults" for h in var)


def test_harvested_inputs_replay_end_to_end():
    b = UnisolatedBackend(parse_profile("u", {"backend": "unisolated", "allowed_targets": ["pkg/*.py::*"], "probe_wall_seconds": 2.0}))
    results = {}
    for q in ("scale", "Stack.pop", "Stack.top", "Stack.__getitem__", "Stack.push", "pairs"):
        res = harvest(T(q), FILES, ["tests/test_mod.py"])
        probes = [h.probe(f"{q}:{i}", T(q)) for i, h in enumerate(res.inputs)]
        obs = run_replay(b, FILES, probes, provenance=FILES).observations
        results[q] = {h.text: obs[p.id] for h, p in zip(res.inputs, probes)}
    infinite = results["pairs"].pop("pairs(itertools.count(1))")
    assert infinite["status"] == "timeout"  # list() over an infinite iterator: cut by the probe timer
    assert all(o["status"] == "ok" for r in results.values() for o in r.values()), results
    val = lambda o: decode_input(o["result"]["value"])  # noqa: E731
    assert val(results["scale"]["scale(more, k=3)"]) == [3, 6, 9, 12]
    assert val(results["scale"]["scale(ys, k=10)"]) == [40, 50]
    assert val(results["Stack.pop"]["s.pop()"]) == 3  # the two pushes ran first
    assert [decode_input(s["value"]) for s in results["Stack.pop"]["s.pop()"]["steps"]] == [2, 3]
    assert val(results["Stack.top"]["s.top"]) == 2
    assert val(results["Stack.__getitem__"]["s[0]"]) == 1
    assert val(results["pairs"]['pairs(iter("abc"))']) == [("a", "b"), ("b", "c")]


@pytest.mark.parametrize("bad", ["__import__('os')", "open('/etc/passwd')"])
def test_unsafe_names_are_not_harvested(bad):
    tests = f"from pkg.mod import scale\n\ndef test_x():\n    scale({bad})\n"
    res = harvest(T("scale"), {**FILES, "tests/t2.py": tests.encode()}, ["tests/t2.py"])
    assert res.inputs == [] or all(bad not in h.text for h in res.inputs if h.path == "tests/t2.py")
