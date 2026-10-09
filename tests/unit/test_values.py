"""Typed value encoding (M3): exact round trips, defensive decoding, and host/harness agreement."""

import enum
import math
import runpy
from importlib import resources

import pytest

from invariantguard.contracts.values import Opaque, ValueError_, decode, encode


def _harness_namespace():
    # The harness is shipped as data; loading its definitions here (without running main) lets
    # the test compare its encoder with the host one. runpy is test-only; the engine never does this.
    path = resources.files("invariantguard.execution").joinpath("probe_harness.py")
    return runpy.run_path(str(path), run_name="ig_harness_under_test")


CORPUS = [None, True, False, 0, -1, 2 ** 80, 1.0, -0.0, 0.1, float("nan"), float("inf"), -float("inf"), "x", "", [1, 2.5, True, None],
          (1, (2, [3])), {"a": [1.0], "b": {"c": None}}, [], ()]


@pytest.mark.parametrize("value", CORPUS, ids=repr)
def test_round_trip_is_exact(value):
    back = decode(encode(value))
    assert type(back) is type(value)
    if isinstance(value, float) and math.isnan(value):
        assert math.isnan(back)
    else:
        assert repr(back) == repr(value)  # distinguishes -0.0, 1 vs 1.0, tuple vs list


def test_harness_encoder_agrees_with_host_encoder():
    ns = _harness_namespace()

    class Custom:
        pass

    cyc = [1]
    cyc.append(cyc)
    for v in CORPUS + [Custom(), cyc, {1: 2}, 10 ** 5000]:
        assert ns["encode"](v) == encode(v)
    for v in CORPUS:
        assert repr(ns["decode"](encode(v))) == repr(decode(encode(v))) or (isinstance(v, float) and math.isnan(v))


class Color(enum.IntEnum):
    RED = 1


class Sneaky:
    def __eq__(self, other):  # pragma: no cover - must never be called
        raise AssertionError("user __eq__ called")

    def __iter__(self):  # pragma: no cover
        raise AssertionError("user __iter__ called")


class MyList(list):
    pass


@pytest.mark.parametrize("value,why", [(Sneaky(), "unsupported type"), (Color.RED, "unsupported type"), (MyList([1]), "unsupported type"),
                                       (10 ** 5000, "integer too large"), ({1: 2}, "non-string key")],
                         ids=["custom", "intenum", "list-subclass", "huge-int", "int-key"])
def test_unsupported_values_become_opaque_without_calling_user_code(value, why):
    enc = encode(value)
    assert enc["t"] == "opaque" and enc["why"] == why
    assert isinstance(decode(enc), Opaque)


def test_cycles_and_depth_are_bounded():
    a = [1]
    a.append(a)
    assert encode(a)["v"][1] == {"t": "opaque", "type": "list", "why": "cyclic value"}
    deep = []
    cur = deep
    for _ in range(100):
        cur.append([])
        cur = cur[0]
    assert "nesting too deep" in str(encode(deep))


@pytest.mark.parametrize("node", [
    {"t": "int", "v": 5},  # not a string
    {"t": "int", "v": "1" * 5000},
    {"t": "int", "v": "--1"},
    {"t": "float", "v": "0x1p+99999"},
    {"t": "float", "v": "zz"},  # not a hex float
    {"t": "code", "v": "__import__('os')"},
    {"t": "list", "v": [{"t": "none"}], "extra": 1},
    {"t": "dict", "v": [[1, {"t": "none"}]]},
    "not a node",
])
def test_malformed_nodes_are_rejected(node):
    with pytest.raises(ValueError_):
        decode(node)


def test_decode_depth_limit():
    node = {"t": "none"}
    for _ in range(200):
        node = {"t": "list", "v": [node]}
    with pytest.raises(ValueError_):
        decode(node)
