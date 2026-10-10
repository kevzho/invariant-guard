"""Phase 3: Hypothesis input generation inside the boundary (shape, inline annotations, .pyi stubs)."""

from __future__ import annotations

import pytest

from invariantguard.execution.profiles import parse_profile
from invariantguard.execution.runner import DockerBackend, UnisolatedBackend
from invariantguard.replay.generate import plan, run_generation
from invariantguard.replay.harvest import harvest, target_kind
from invariantguard.replay.run import Expr, Target, Value, run_replay
from invariantguard.replay.values import decode_input

MOD = b'''
def scale(xs, k=2):
    return [x * k for x in xs]


def clip(x: int, lo: int = 0, hi: int = 10) -> int:
    return max(lo, min(hi, x))


def combos(iterable, r):
    pool = tuple(iterable)
    if not pool and r:
        return [()]          # the caaec94-style special case
    return [pool[:r]] if r <= len(pool) else []


class Acc:
    def __init__(self, start=0):
        self.total = start

    def add(self, n: int) -> int:
        self.total += n
        return self.total
'''
STUB = b'''from typing import Iterable, TypeVar

_T = TypeVar("_T")

def combos(iterable: Iterable[_T], r: int) -> list[tuple[_T, ...]]: ...
'''
TESTS = b'''from pkg.mod import scale, Acc

def test_scale():
    assert scale([1, 2], 3) == [3, 6]
    assert scale("ab")

def test_acc():
    a = Acc(5)
    assert a.add(1) == 6
'''
FILES = {"pkg/__init__.py": b"", "pkg/mod.py": MOD, "pkg/mod.pyi": STUB, "tests/test_mod.py": TESTS}
T = lambda q: Target("pkg/mod.py", q)  # noqa: E731
IMAGE = "invariantguard-diffreplay:spike"  # python:3.13-slim + pinned hypothesis (spikes/diffreplay/Dockerfile)


def _plan(q):
    import ast

    kind = target_kind(ast.parse(MOD), q)
    h = harvest(T(q), FILES, ["tests/test_mod.py"])
    return plan(T(q), kind, FILES, h.inputs, n=15)


def test_planning_is_static_and_covers_each_source():
    jobs, _ = _plan("scale")
    assert [j.origin for j in jobs] == ["shape", "shape"]  # one per distinct literal shape: ([1, 2], 3) and ("ab",)
    jobs, stats = _plan("clip")
    assert [j.origin for j in jobs] == ["annotation"] and set(jobs[0].vary) == {"k:x", "k:lo", "k:hi"}
    jobs, _ = _plan("combos")
    assert [j.origin for j in jobs] == ["stub"]
    stub = jobs[0].vary["k:iterable"]
    assert stub["ann"] == "Iterable[_T]" and stub["typevars"] == ["_T"] and ["Iterable", "typing", "Iterable"] in stub["imports"]
    jobs, _ = _plan("Acc.add")
    assert {j.origin for j in jobs} == {"shape", "annotation"} and next(j for j in jobs if j.origin == "annotation").receiver is not None


def test_generation_without_hypothesis_is_reported_unavailable():
    b = UnisolatedBackend(parse_profile("u", {"backend": "unisolated", "allowed_targets": ["pkg/*.py::*"]}))
    jobs, _ = _plan("clip")
    g = run_generation(b, FILES, jobs)
    assert g.inputs == [] and g.stats.get("annotation_unavailable") == 1


@pytest.fixture(scope="module")
def docker_gen():
    b = DockerBackend(parse_profile("g", {"backend": "docker", "image": IMAGE, "allowed_targets": ["pkg/*.py::*"], "site_packages": True,
                                          "memory_mb": 512, "max_processes": 32, "wall_seconds": 300, "cpu_seconds": 60}))
    if not b.status().available:
        pytest.skip(f"image with hypothesis unavailable here: {b.status().reason}")
    return b


def test_generation_in_docker_is_deterministic_and_replayable(docker_gen):
    jobs = [j for q in ("scale", "clip", "combos", "Acc.add") for j in _plan(q)[0]]
    g1 = run_generation(docker_gen, FILES, jobs)
    g2 = run_generation(docker_gen, FILES, jobs)
    assert [h.key() for h in g1.inputs] == [h.key() for h in g2.inputs]  # derandomized, no database
    by = {}
    for h in g1.inputs:
        by.setdefault(h.text.split(" [")[0] + " " + h.origin, []).append(h)
    assert any(k.endswith("shape") for k in by) and any(k.endswith("annotation") for k in by) and any(k.endswith("stub") for k in by)
    shape = by["scale([1, 2], 3) shape"]
    assert len(shape) > 3 and all(isinstance(a, Value) for h in shape for a in h.args)
    assert all(type(decode_input(h.args[0].node)) is list for h in shape)
    clip = [h for h in g1.inputs if h.origin == "annotation" and "clip" in h.text]
    assert clip and all(type(decode_input(v.node)) is int for h in clip for _, v in h.kwargs)
    add = [h for h in g1.inputs if h.origin == "annotation" and "Acc.add" in h.text]
    assert add and all(isinstance(h.receiver, Expr) and h.receiver.text == "a" for h in add)
    # The generated inputs run like harvested ones; the stub finds the empty-pool special case.
    probes = [h.probe(f"p{i}", h.target) for i, h in enumerate(g1.inputs)]
    obs = run_replay(docker_gen, FILES, probes, provenance=FILES).observations
    assert all(o["status"] == "ok" for o in obs.values()), [o for o in obs.values() if o["status"] != "ok"][:3]
    combos = [(h, obs[p.id]) for h, p in zip(g1.inputs, probes) if h.origin == "stub"]
    assert any(decode_input(o["result"]["value"]) == [()] for _, o in combos)
