"""Path-sensitive interval/nonzero abstract interpretation over the acyclic IR CFG.

Each block's entry state is the join of its predecessors' exit states after refining
them with the branch outcome that leads to the block. A join keeps only facts true on
every feasible incoming edge. Infeasible edges contribute bottom.

Sequence lengths (M2) live under ``len:<value>`` keys. A list's length fact is reset to
``[0, +inf]`` at every instruction with an unknown effect (it may mutate any list that
escaped); tuple lengths never change. A ``LEN`` result refines its sequence's length
only while no unknown effect happened in between (tracked by an ``epoch`` key).
"""

from __future__ import annotations

from dataclasses import dataclass

import itertools

from ..ir.model import DIVISION_OPS, EXACT_NON_NONE, NUMERIC_EXACT, SEQUENCE_KINDS, Effect, Function, Instruction, Kind, Op
from .intervals import NEGATE, SWAP, AbsInt, Interval

State = dict[str, AbsInt]  # value id -> fact (numeric exact values only)

BOOL_TOP = AbsInt.mk(Interval(0, 1))
TRUE = AbsInt.const(1)
FALSE = AbsInt.const(0)


@dataclass
class DivisionFact:
    instr: Instruction
    block_id: str
    reachable: bool
    divisor: AbsInt | None  # None if the divisor is not an exact int/bool
    divisor_kind: Kind
    dividend_kind: Kind
    divisor_const: object  # literal value when the divisor is a CONST


@dataclass
class SiteFact:
    """Facts at an index or attribute obligation (M2)."""

    instr: Instruction
    block_id: str
    reachable: bool
    seq_kind: Kind | None = None
    seq_len: AbsInt | None = None
    index_kind: Kind | None = None
    index: AbsInt | None = None
    recv_kind: Kind | None = None


_EPOCHS = itertools.count(1)
EPOCH = "epoch"


def len_key(v: str) -> str:
    return "len:" + v


class IntervalAnalysis:
    def __init__(self, fn: Function) -> None:
        self.fn = fn
        self.defs = fn.instr_by_result()
        self.in_states: dict[str, State | None] = {}
        self.out_states: dict[str, State | None] = {}
        self.edge_states: dict[tuple[str, str], State | None] = {}
        self.division_facts: dict[str, DivisionFact] = {}
        self.site_facts: dict[str, SiteFact] = {}

    def kind(self, v: str) -> Kind:
        ins = self.defs.get(v)
        if ins is None or ins.type is None:
            return Kind.OBJECT
        return ins.type.kind

    # -- driver ------------------------------------------------------------------------
    def run(self) -> "IntervalAnalysis":
        for bid in self.fn.topo_order():
            block = self.fn.blocks[bid]
            if bid == self.fn.entry_id:
                state: State | None = {EPOCH: AbsInt.const(0)}
            else:
                incoming = [self.edge_states.get((p, bid)) for p in block.preds]
                state = self._join([s for s in incoming if s is not None])
            self.in_states[bid] = None if state is None else dict(state)
            if state is not None:
                state = dict(state)
                for ins in block.instructions:
                    self._transfer(ins, state, bid)
                    if state is None:
                        break
            else:
                for ins in block.instructions:
                    if ins.op in DIVISION_OPS:
                        self._record_division(ins, None, bid)
                    elif ins.attrs.get("obligation_property"):
                        self._record_site(ins, None, bid)
            self.out_states[bid] = state
            self._edges(block, state)
        return self

    @staticmethod
    def _join(states: list[State]) -> State | None:
        if not states:
            return None
        keys = set(states[0])
        for s in states[1:]:
            keys &= set(s)
        out: State = {}
        for k in keys:
            acc = states[0][k]
            for s in states[1:]:
                acc = acc.join(s[k])
            out[k] = acc
        return out

    # -- transfer ----------------------------------------------------------------------
    def _havoc_lists(self, state: State) -> None:
        for key in list(state):
            if key.startswith("len:") and self.kind(key[4:]) is Kind.LIST:
                state[key] = AbsInt.mk(Interval(0, Interval.top().hi))
        state[EPOCH] = AbsInt.const(next(_EPOCHS))

    def _transfer(self, ins: Instruction, state: State, bid: str) -> None:
        op = ins.op
        r = ins.result
        k = ins.type.kind if ins.type else Kind.OBJECT
        if ins.effect is Effect.UNKNOWN:
            # Unknown code may mutate any list that escaped (aliases are not tracked).
            self._havoc_lists(state)
        if op is Op.PARAM:
            if k is Kind.INT:
                state[r] = AbsInt.top()
            elif k is Kind.BOOL:
                state[r] = BOOL_TOP
            elif k in SEQUENCE_KINDS:
                state[len_key(r)] = AbsInt.mk(Interval(0, Interval.top().hi))
        elif op is Op.BUILD_SEQ:
            state[len_key(r)] = AbsInt.const(len(ins.operands))
        elif op is Op.ASSIGN and not ins.attrs.get("checked_read"):
            src = ins.operands[0]
            if src in state and k in NUMERIC_EXACT:
                state[r] = state[src]
            elif k is Kind.INT:
                state[r] = AbsInt.top()
            elif k is Kind.BOOL:
                state[r] = BOOL_TOP
            if len_key(src) in state and k in SEQUENCE_KINDS:
                state[len_key(r)] = state[len_key(src)]
        elif op is Op.LEN:
            seq = ins.operands[0]
            state[r] = state.get(len_key(seq), AbsInt.mk(Interval(0, Interval.top().hi)))
            if len_key(seq) in state:
                state["lenof:" + r] = AbsInt.const(int(seq[1:]) if seq[1:].isdigit() else -1)
                state["lenepoch:" + r] = state.get(EPOCH, AbsInt.top())
        elif op is Op.INDEX and ins.attrs.get("obligation_property"):
            self._record_site(ins, state, bid)
            seq, idx = ins.operands
            lk = len_key(seq)
            if lk in state:
                # The path continues only if the index was in bounds: the sequence is
                # non-empty, and longer than a constant index.
                lo = 1
                c = state[idx].itv.singleton() if idx in state else None
                if c is not None:
                    lo = max(lo, c + 1, -c)
                state[lk] = state[lk].meet(AbsInt.mk(Interval(lo, Interval.top().hi)))
        elif op is Op.ATTRIBUTE and ins.attrs.get("obligation_property"):
            self._record_site(ins, state, bid)
        elif op is Op.CONST:
            v = ins.attrs.get("value")
            if isinstance(v, bool):
                state[r] = TRUE if v else FALSE
            elif isinstance(v, int):
                state[r] = AbsInt.const(v)
        elif op in (Op.ADD, Op.MUL) and k in SEQUENCE_KINDS:
            # Built-in sequence concatenation / repetition (lowering types only exact cases).
            unknown_len = AbsInt.mk(Interval(0, Interval.top().hi))
            a, b = ins.operands
            if op is Op.ADD:
                la, lb = state.get(len_key(a)), state.get(len_key(b))
                state[len_key(r)] = la.add(lb) if la is not None and lb is not None else unknown_len
            else:
                seq, cnt = (a, b) if self.kind(a) in SEQUENCE_KINDS else (b, a)
                ls, c = state.get(len_key(seq)), state.get(cnt)
                n = ls.itv.singleton() if ls is not None else None
                m = c.itv.singleton() if c is not None else None
                state[len_key(r)] = AbsInt.const(n * max(m, 0)) if n is not None and m is not None else unknown_len
        elif op in (Op.ADD, Op.SUB, Op.MUL) and k is Kind.INT:
            a, b = state.get(ins.operands[0]), state.get(ins.operands[1])
            if a is not None and b is not None:
                state[r] = a.add(b) if op is Op.ADD else a.sub(b) if op is Op.SUB else a.mul(b)
            else:
                state[r] = AbsInt.top()
        elif op in DIVISION_OPS:
            self._record_division(ins, state, bid)
            a, b = state.get(ins.operands[0]), state.get(ins.operands[1])
            if k is Kind.INT:
                c = b.itv.singleton() if b is not None else None
                if a is not None and c not in (None, 0):
                    state[r] = a.floordiv_const(c) if op is Op.FLOORDIV else a.mod_const(c)
                else:
                    state[r] = AbsInt.top()
            # The path continues only if the divisor was nonzero -- but only for a built-in
            # numeric operation. With an operand of unmodeled type the operator may dispatch
            # to user code (e.g. ``obj.__truediv__``) that accepts a zero divisor without
            # raising, so nothing is learned about the divisor (audit A-07).
            if b is not None and ins.flavor != "python.dynamic_dispatch":
                refined = b.meet(AbsInt.mk(Interval.top(), True))
                state[ins.operands[1]] = refined
        elif op is Op.COMPARE and k is Kind.BOOL:
            a, b = state.get(ins.operands[0]), state.get(ins.operands[1])
            state[r] = self._decide(ins.attrs["cmp"], a, b)
        elif op is Op.IS_NONE:
            sk = self.kind(ins.operands[0])
            if sk is Kind.NONE:
                state[r] = TRUE
            elif sk in EXACT_NON_NONE:
                state[r] = FALSE
            else:
                # OBJECT, OPTIONAL and any other kind may be None. (Whitelist: an M1 rule
                # "everything but OBJECT is non-None" became a false proof once OPTIONAL
                # values existed; found by tests/differential/test_m2_differential.py.)
                state[r] = BOOL_TOP
        elif op is Op.PHI and k in SEQUENCE_KINDS and not ins.attrs.get("maybe_unbound"):
            lens = []
            for pred, val in ins.attrs["incoming"]:
                es = self.edge_states.get((pred, bid))
                if es is None:
                    continue
                f = es.get(len_key(val)) if val else None
                if f is None:
                    lens = []
                    break
                lens.append(f)
            if lens:
                acc = lens[0]
                for f in lens[1:]:
                    acc = acc.join(f)
                state[len_key(r)] = acc
            else:
                state[len_key(r)] = AbsInt.mk(Interval(0, Interval.top().hi))
        elif op is Op.PHI:
            facts = []
            ok = not ins.attrs.get("maybe_unbound") and k in NUMERIC_EXACT
            if ok:
                for pred, val in ins.attrs["incoming"]:
                    es = self.edge_states.get((pred, bid))
                    if es is None:
                        continue  # infeasible edge
                    f = es.get(val)
                    if f is None:
                        ok = False
                        break
                    facts.append(f)
            if ok and facts:
                acc = facts[0]
                for f in facts[1:]:
                    acc = acc.join(f)
                state[r] = acc
            elif k is Kind.INT:
                state[r] = AbsInt.top()
            elif k is Kind.BOOL:
                state[r] = BOOL_TOP
        elif r is not None and k is Kind.BOOL:
            state[r] = BOOL_TOP
        elif r is not None and k is Kind.INT:
            state[r] = AbsInt.top()

    @staticmethod
    def _decide(cmp: str, a: AbsInt | None, b: AbsInt | None) -> AbsInt:
        if a is None or b is None or a.bottom or b.bottom:
            return BOOL_TOP
        x, y = a.itv, b.itv
        sx, sy = x.singleton(), y.singleton()
        if cmp in ("EQ", "NE"):
            if sx is not None and sy is not None:
                eq = sx == sy
            elif x.meet(y).bottom or (sy == 0 and a.nonzero) or (sx == 0 and b.nonzero):
                eq = False
            else:
                return BOOL_TOP
            return TRUE if (eq if cmp == "EQ" else not eq) else FALSE
        if cmp == "LT":
            return TRUE if x.hi < y.lo else FALSE if x.lo >= y.hi else BOOL_TOP
        if cmp == "LE":
            return TRUE if x.hi <= y.lo else FALSE if x.lo > y.hi else BOOL_TOP
        if cmp == "GT":
            return TRUE if x.lo > y.hi else FALSE if x.hi <= y.lo else BOOL_TOP
        if cmp == "GE":
            return TRUE if x.lo >= y.hi else FALSE if x.hi < y.lo else BOOL_TOP
        return BOOL_TOP

    def _record_division(self, ins: Instruction, state: State | None, bid: str) -> None:
        divisor = ins.operands[1]
        dk = self.kind(divisor)
        ddef = self.defs.get(divisor)
        dconst = ddef.attrs.get("value") if ddef is not None and ddef.op is Op.CONST else None
        fact = state.get(divisor) if state is not None else None
        self.division_facts[ins.result] = DivisionFact(
            instr=ins,
            block_id=bid,
            reachable=state is not None,
            divisor=fact if dk in NUMERIC_EXACT else None,
            divisor_kind=dk,
            dividend_kind=self.kind(ins.operands[0]),
            divisor_const=dconst,
        )

    def _record_site(self, ins: Instruction, state: State | None, bid: str) -> None:
        fact = SiteFact(instr=ins, block_id=bid, reachable=state is not None)
        if ins.op is Op.INDEX:
            seq, idx = ins.operands
            fact.seq_kind = self.kind(seq)
            fact.index_kind = self.kind(idx)
            if state is not None:
                fact.seq_len = state.get(len_key(seq))
                fact.index = state.get(idx) if fact.index_kind in NUMERIC_EXACT else None
        else:
            fact.recv_kind = self.kind(ins.operands[0])
        self.site_facts[ins.result] = fact

    def _refine_len_link(self, state: State, v: str) -> None:
        """After refining a LEN result, refine its sequence's length if still current."""
        link = state.get("lenof:" + v)
        if link is None:
            return
        seq_id = link.itv.singleton()
        if seq_id is None or seq_id < 0:
            return
        seq = f"v{seq_id}"
        if self.kind(seq) is Kind.LIST:
            then, now = state.get("lenepoch:" + v), state.get(EPOCH)
            if then is None or now is None or then.itv.singleton() is None or then.itv.singleton() != now.itv.singleton():
                return  # an unknown effect may have changed the list since len() was taken
        lk = len_key(seq)
        if lk in state and v in state:
            state[lk] = state[lk].meet(AbsInt.mk(state[v].itv))

    # -- edges -------------------------------------------------------------------------
    def _edges(self, block, state: State | None) -> None:
        term = block.terminator
        if term is None:
            return
        if term.op is Op.JUMP:
            self.edge_states[(block.block_id, term.attrs["target"])] = state
            return
        if term.op is not Op.BRANCH:
            return
        t, f = term.attrs["true_target"], term.attrs["false_target"]
        if state is None:
            self.edge_states[(block.block_id, t)] = None
            self.edge_states[(block.block_id, f)] = None
            return
        cond = term.operands[0]
        self.edge_states[(block.block_id, t)] = self._assume(dict(state), cond, True)
        self.edge_states[(block.block_id, f)] = self._assume(dict(state), cond, False)

    def _assume(self, state: State, cond: str, outcome: bool) -> State | None:
        want = TRUE if outcome else FALSE
        cf = state.get(cond)
        if cf is not None:
            m = cf.meet(want)
            if m.bottom:
                return None
            state[cond] = m
        ins = self.defs.get(cond)
        if ins is None:
            return state
        if ins.op is Op.COMPARE and ins.attrs.get("cmp") in NEGATE:
            a, b = ins.operands
            if self.kind(a) in NUMERIC_EXACT and self.kind(b) in NUMERIC_EXACT:
                cmp = ins.attrs["cmp"] if outcome else NEGATE[ins.attrs["cmp"]]
                fa, fb = state.get(a), state.get(b)
                if fa is not None and fb is not None:
                    na = fa.refine(cmp, fb)
                    nb = fb.refine(SWAP[cmp], fa)
                    if na.bottom or nb.bottom:
                        return None
                    state[a], state[b] = na, nb
                    self._refine_len_link(state, a)
                    self._refine_len_link(state, b)
            # `x is not None` is lowered as COMPARE EQ (IS_NONE x) False: refine through it.
            if ins.attrs.get("synthesized") == "is_not":
                inner = self.defs.get(a)
                if inner is not None and inner.op is Op.IS_NONE:
                    return self._assume(state, a, not outcome)
        return state
