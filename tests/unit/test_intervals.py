import math

from invariantguard.analysis.intervals import AbsInt, Interval

INF = math.inf


def test_top_bottom_and_join_meet():
    top, bot = Interval.top(), Interval.bot()
    assert top.is_top() and bot.bottom
    a = Interval(1, 5)
    assert a.join(bot) == a and bot.join(a) == a
    assert a.meet(bot).bottom
    assert a.meet(Interval(6, 9)).bottom
    assert a.meet(Interval(3, INF)) == Interval(3, 5)
    assert a.join(Interval(-2, 0)) == Interval(-2, 5)


def test_infinite_bound_arithmetic():
    a = Interval(-INF, 3)
    b = Interval(2, INF)
    assert a.add(b) == Interval(-INF, INF)
    assert a.sub(b) == Interval(-INF, 1)
    assert Interval(0, 0).mul(Interval(-INF, INF)) == Interval(0, 0)  # 0 * inf bound = 0
    assert Interval(-2, 3).mul(Interval(4, 5)) == Interval(-10, 15)
    assert Interval(2, INF).mul(Interval(-3, -1)) == Interval(-INF, -2)


def test_nonzero_fact_is_separate_from_interval():
    x = AbsInt.mk(Interval.top(), nonzero=True)
    assert x.excludes_zero() and x.itv.contains(0)  # the interval alone cannot express the gap
    assert not AbsInt.top().excludes_zero()
    # join keeps nonzero only when both sides are nonzero
    assert not x.join(AbsInt.const(0)).excludes_zero()
    assert x.join(AbsInt.const(7)).excludes_zero()
    # meet keeps nonzero if either side has it; nonzero meet [0,0] is bottom
    assert AbsInt.const(0).meet(x).bottom


def test_nonzero_shrinks_zero_endpoints():
    assert AbsInt.mk(Interval(0, 10), True).itv == Interval(1, 10)
    assert AbsInt.mk(Interval(-4, 0), True).itv == Interval(-4, -1)
    assert AbsInt.mk(Interval(0, 0), True).bottom


def test_refinement_boundaries():
    x = AbsInt.top()
    assert x.refine("GT", AbsInt.const(5)).itv == Interval(6, INF)
    assert x.refine("GE", AbsInt.const(5)).itv == Interval(5, INF)
    assert x.refine("LT", AbsInt.const(3)).itv == Interval(-INF, 2)
    assert x.refine("LE", AbsInt.const(3)).itv == Interval(-INF, 3)
    assert x.refine("NE", AbsInt.const(0)).excludes_zero()
    assert x.refine("EQ", AbsInt.const(0)).is_zero()
    # x > 5 then x < 3 is bottom: unreachable
    assert x.refine("GT", AbsInt.const(5)).refine("LT", AbsInt.const(3)).bottom
    # NE with a nonzero constant only trims endpoints
    assert AbsInt.mk(Interval(4, 9)).refine("NE", AbsInt.const(4)).itv == Interval(5, 9)


def test_add_does_not_invent_nonzero():
    # [1,inf] + [-inf,-1] may be zero
    r = AbsInt.mk(Interval(1, INF)).add(AbsInt.mk(Interval(-INF, -1)))
    assert not r.excludes_zero()
    # product of nonzero values is nonzero
    assert AbsInt.mk(Interval.top(), True).mul(AbsInt.mk(Interval.top(), True)).excludes_zero()


def test_python_floor_and_mod_by_constant():
    # Python floors toward -inf: [-3, 3] // 2 == [-2, 1]
    assert AbsInt.mk(Interval(-3, 3)).floordiv_const(2).itv == Interval(-2, 1)
    assert AbsInt.mk(Interval(-3, 3)).floordiv_const(-2).itv == Interval(-2, 1)
    for a in range(-3, 4):
        assert a // 2 in range(-2, 2) and a // -2 in range(-2, 2)
    assert AbsInt.top().mod_const(5).itv == Interval(0, 4)
    assert AbsInt.top().mod_const(-5).itv == Interval(-4, 0)
    for a in range(-20, 20):
        assert 0 <= a % 5 <= 4 and -4 <= a % -5 <= 0
