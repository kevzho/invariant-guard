"""Optional Z3 adapter.

* z3 is imported lazily; when it is missing the capability is reported as unavailable
  and the analysis still runs (intervals + bounded witness search).
* Queries are built only from typed IR through the z3 Python API. No SMT text from any
  other source (including models) is ever parsed.
* Each query has a time limit (and optional resource limit), a fixed random seed, and
  a digest of the exact assertion set. SAT / UNSAT / UNKNOWN / TIMEOUT / RESOURCE_LIMIT /
  UNAVAILABLE are kept distinct.
"""

from __future__ import annotations

import hashlib
import importlib
import time
from dataclasses import dataclass, field
from typing import Any

from ..ir.model import EXACT_NON_NONE, NUMERIC_EXACT, SEQUENCE_KINDS, Effect, Function, Instruction, Kind, Op
from ..models.enums import SolverStatus


@dataclass
class SolverCapability:
    available: bool
    name: str = "z3"
    version: str | None = None
    reason: str | None = None


def probe_z3() -> SolverCapability:
    try:
        z3 = importlib.import_module("z3")
        version = z3.get_version_string()
    except Exception as exc:  # ImportError, or a broken installation
        return SolverCapability(False, reason=f"{type(exc).__name__}: {exc}")
    return SolverCapability(True, version=version)


@dataclass
class SolverOutcome:
    status: SolverStatus
    model: dict[str, Any] | None = None
    query_digest: str | None = None
    reason: str | None = None
    elapsed_ms: float = 0.0
    timeout_ms: int | None = None
    rlimit: int | None = None
    exact: bool = True  # False when havoc (overapproximation) entered the query
    havoc_values: list[str] = field(default_factory=list)


class Z3In:
    """The subset of the z3 API used by the encoder, bound to one ``z3.Context``.

    Each analyzed function gets its own context, so a query's model depends only on that
    function's (deterministic) query sequence, not on what else ran in the process
    (z3's global context makes models depend on the history of created terms).
    """

    def __init__(self, z3: Any, ctx: Any) -> None:
        self._z3 = z3
        self.ctx = ctx
        self.sat, self.unsat = z3.sat, z3.unsat
        self.is_true, self.is_bool = z3.is_true, z3.is_bool

    def Int(self, name: str) -> Any:
        return self._z3.Int(name, self.ctx)

    def Bool(self, name: str) -> Any:
        return self._z3.Bool(name, self.ctx)

    def IntVal(self, v: int) -> Any:
        return self._z3.IntVal(v, self.ctx)

    def BoolVal(self, v: bool) -> Any:
        return self._z3.BoolVal(v, self.ctx)

    def If(self, c: Any, a: Any, b: Any) -> Any:
        return self._z3.If(c, a, b, self.ctx)

    def And(self, *args: Any) -> Any:
        return self._z3.And(*args) if args else self.BoolVal(True)

    def Or(self, *args: Any) -> Any:
        return self._z3.Or(*args) if args else self.BoolVal(False)

    def Not(self, a: Any) -> Any:
        return self._z3.Not(a, self.ctx)

    def Implies(self, a: Any, b: Any) -> Any:
        return self._z3.Implies(a, b, self.ctx)

    def Solver(self) -> Any:
        return self._z3.Solver(ctx=self.ctx)


class FunctionEncoder:
    """Encodes reachability of one obligation in one function as a Z3 formula.

    A fresh encoder is used per query, so ``havoc`` lists exactly the unmodeled values
    referenced by that query (the query's cone of influence).
    """

    def __init__(self, z3: Any, fn: Function) -> None:
        self.z3 = z3
        self.fn = fn
        self.defs = fn.instr_by_result()
        self.def_block = fn.def_block()
        self.memo: dict[str, Any] = {}
        self.reach_memo: dict[str, Any] = {}
        self.havoc: list[str] = []
        self.side: list[Any] = []
        self.params: dict[str, Any] = {}
        self.seq_params: dict[str, tuple[Any, Kind]] = {}  # name -> (length var, kind)
        self.none_params: dict[str, Any] = {}  # name -> is-None var of an OPTIONAL parameter
        self.position: dict[str, tuple[str, int]] = {}
        for b in fn.blocks.values():
            for i, ins in enumerate(b.instructions):
                if ins.result is not None:
                    self.position[ins.result] = (b.block_id, i)
        self.len_memo: dict[str, Any] = {}
        self.none_memo: dict[str, Any] = {}

    # -- values ------------------------------------------------------------------------
    def kind(self, v: str) -> Kind:
        ins = self.defs.get(v)
        return ins.type.kind if ins is not None and ins.type is not None else Kind.OBJECT

    def _havoc(self, v: str, sort: str) -> Any:
        if v not in self.havoc:
            self.havoc.append(v)
        name = f"havoc_{sort}_{v}"
        return self.z3.Int(name) if sort == "int" else self.z3.Bool(name)

    def int_term(self, v: str) -> Any:
        k = self.kind(v)
        ins = self.defs.get(v)
        if k is Kind.OPTIONAL and ins is not None and ins.type is not None:
            k = ins.type.inner or Kind.OBJECT
        t = self.term(v)
        if t is None:
            return self._havoc(v, "int")
        if k is Kind.BOOL:
            return self.z3.If(t, self.z3.IntVal(1), self.z3.IntVal(0))
        return t

    # -- sequences and None (M2) ------------------------------------------------------------
    def unstable(self, bid: str, upto: int) -> bool:
        """True when an unknown effect may run before this point (it may mutate lists)."""
        key = ("unstable", bid, upto)
        if key not in self.reach_memo:
            self.reach_memo[key] = bool(self.upstream_unknown_effects(bid, upto))
        return self.reach_memo[key]

    def _havoc_len(self, key: str) -> Any:
        if key not in self.havoc:
            self.havoc.append(key)
        var = self.z3.Int(f"havoc_len_{key}")
        self.side.append(var >= 0)
        return var

    def seq_len(self, v: str, bid: str, upto: int) -> Any:
        """Length of sequence ``v`` at the point before instruction ``upto`` of ``bid``."""
        if self.kind(v) is Kind.LIST and self.unstable(bid, upto):
            return self._havoc_len(f"{v}_at_{bid}_{upto}")
        return self.seq_len_base(v)

    def seq_len_base(self, v: str) -> Any:
        if v in self.len_memo:
            return self.len_memo[v]
        z3 = self.z3
        ins = self.defs.get(v)
        out: Any
        if ins is None or self.kind(v) not in SEQUENCE_KINDS:
            out = self._havoc_len(v)
        elif ins.op is Op.BUILD_SEQ:
            out = z3.IntVal(len(ins.operands))
        elif ins.op is Op.PARAM:
            name = ins.attrs["name"]
            out = z3.Int(f"param_len!{name}")
            self.side.append(out >= 0)
            self.seq_params[name] = (out, self.kind(v))
        elif ins.op is Op.ASSIGN and not ins.attrs.get("checked_read"):
            out = self.seq_len_base(ins.operands[0])
        elif ins.op is Op.ADD and ins.flavor == "python.sequence_concat" and not ins.attrs.get("augmented"):
            out = self.seq_len_base(ins.operands[0]) + self.seq_len_base(ins.operands[1])
        elif ins.op is Op.PHI and not ins.attrs.get("maybe_unbound"):
            bid = self.def_block[v]
            incoming = ins.attrs["incoming"]
            out = self.seq_len_base(incoming[-1][1])
            for pred, val in reversed(incoming[:-1]):
                out = z3.If(self.edge(pred, bid), self.seq_len_base(val), out)
        else:
            out = self._havoc_len(v)
        self.len_memo[v] = out
        return out

    def none_term(self, v: str) -> Any:
        """Boolean: value ``v`` is None."""
        if v in self.none_memo:
            return self.none_memo[v]
        z3 = self.z3
        k = self.kind(v)
        ins = self.defs.get(v)
        out: Any
        if k is Kind.NONE:
            out = z3.BoolVal(True)
        elif k in EXACT_NON_NONE:
            out = z3.BoolVal(False)
        elif ins is None or k is not Kind.OPTIONAL:
            out = self._havoc(v, "none")
        elif ins.op is Op.PARAM:
            name = ins.attrs["name"]
            out = z3.Bool(f"param_none!{name}")
            self.none_params[name] = out
        elif ins.op is Op.ASSIGN and not ins.attrs.get("checked_read"):
            out = z3.BoolVal(False) if ins.attrs.get("narrowed") else self.none_term(ins.operands[0])
        elif ins.op is Op.PHI and not ins.attrs.get("maybe_unbound"):
            bid = self.def_block[v]
            incoming = ins.attrs["incoming"]
            out = self.none_term(incoming[-1][1])
            for pred, val in reversed(incoming[:-1]):
                out = z3.If(self.edge(pred, bid), self.none_term(val), out)
        else:
            out = self._havoc(v, "none")
        self.none_memo[v] = out
        return out

    def in_bounds(self, ins: Instruction, bid: str, upto: int) -> Any:
        """``-len <= i < len`` for an exact built-in sequence index (Python semantics)."""
        seq, idx = ins.operands
        n = self.seq_len(seq, bid, upto)
        i = self.int_term(idx) if self.kind(idx) in NUMERIC_EXACT else self._havoc(idx, "int")
        return self.z3.And(-n <= i, i < n)

    def obligation_ok(self, ins: Instruction, bid: str, upto: int) -> Any | None:
        """Fact that holds after an obligation instruction executes without failing."""
        if ins.op in (Op.DIV, Op.FLOORDIV, Op.MOD):
            return self.division_guard(ins)
        prop = ins.attrs.get("obligation_property")
        if ins.op is Op.INDEX and prop:
            return self.in_bounds(ins, bid, upto)
        if ins.op is Op.ATTRIBUTE and prop:
            return self.z3.Not(self.none_term(ins.operands[0]))
        return None

    def bool_term(self, v: str) -> Any:
        t = self.term(v)
        k = self.kind(v)
        ins = self.defs.get(v)
        if k is Kind.OPTIONAL and ins is not None and ins.type is not None:
            k = ins.type.inner or Kind.OBJECT
        if t is None or k is not Kind.BOOL:
            return self._havoc(v, "bool")
        return t

    def term(self, v: str) -> Any:
        if v in self.memo:
            return self.memo[v]
        self.memo[v] = None  # guard against accidental recursion
        t = self._build(self.defs[v]) if v in self.defs else None
        self.memo[v] = t
        return t

    def _build(self, ins: Instruction) -> Any:
        z3 = self.z3
        op = ins.op
        k = ins.type.kind if ins.type else Kind.OBJECT
        if op is Op.PARAM:
            name = ins.attrs["name"]
            # Namespace source names: a Python parameter may be spelled like an internal
            # constant (e.g. ``floordiv_q_v3``); Z3 identifies constants by name, so an
            # unprefixed name would silently alias them (audit finding A-01).
            sym = f"param!{name}"
            if k is Kind.OPTIONAL and ins.type is not None:
                k = ins.type.inner or Kind.OBJECT  # value part when not None
            if k is Kind.INT:
                var = z3.Int(sym)
            elif k is Kind.BOOL:
                var = z3.Bool(sym)
            else:
                return None
            self.params[name] = var
            return var
        if op is Op.CONST:
            val = ins.attrs.get("value")
            if isinstance(val, bool):
                return z3.BoolVal(val)
            if isinstance(val, int):
                return z3.IntVal(val)
            return None
        if op in (Op.ADD, Op.SUB, Op.MUL) and k is Kind.INT:
            a, b = ins.operands
            if self.kind(a) not in NUMERIC_EXACT or self.kind(b) not in NUMERIC_EXACT:
                return None
            x, y = self.int_term(a), self.int_term(b)
            return x + y if op is Op.ADD else x - y if op is Op.SUB else x * y
        if op in (Op.FLOORDIV, Op.MOD) and k is Kind.INT:
            a, b = ins.operands
            x, y = self.int_term(a), self.int_term(b)
            q, r = z3.Int(f"floordiv_q_{ins.result}"), z3.Int(f"floordiv_r_{ins.result}")
            # Python floor division: a == q*b + r, remainder has the divisor's sign.
            self.side.append(
                z3.Implies(
                    y != 0,
                    z3.And(
                        x == q * y + r,
                        z3.Implies(y > 0, z3.And(r >= 0, r < y)),
                        z3.Implies(y < 0, z3.And(r <= 0, r > y)),
                    ),
                )
            )
            return q if op is Op.FLOORDIV else r
        if op is Op.COMPARE and k is Kind.BOOL:
            a, b = ins.operands
            if self.kind(a) not in NUMERIC_EXACT or self.kind(b) not in NUMERIC_EXACT:
                return None
            x, y = self.int_term(a), self.int_term(b)
            cmp = ins.attrs["cmp"]
            return {"EQ": x == y, "NE": x != y, "LT": x < y, "LE": x <= y, "GT": x > y, "GE": x >= y}[cmp]
        if op is Op.IS_NONE:
            sk = self.kind(ins.operands[0])
            if sk is Kind.NONE:
                return z3.BoolVal(True)
            if sk is Kind.OPTIONAL:
                return self.none_term(ins.operands[0])
            if sk in EXACT_NON_NONE:
                return z3.BoolVal(False)
            return None  # OBJECT or any other kind: havoc
        if op is Op.LEN:
            bid, idx = self.position[ins.result]
            return self.seq_len(ins.operands[0], bid, idx)
        if op is Op.ASSIGN and not ins.attrs.get("checked_read"):
            src = ins.operands[0]
            if k is Kind.INT:
                return self.int_term(src)
            if k is Kind.BOOL:
                return self.bool_term(src)
            if k is Kind.OPTIONAL and ins.type is not None and ins.type.inner in NUMERIC_EXACT:
                return self.term(src)
            return None
        if op is Op.PHI and k in NUMERIC_EXACT and not ins.attrs.get("maybe_unbound"):
            bid = self.def_block[ins.result]
            incoming = ins.attrs["incoming"]
            conv = self.int_term if k is Kind.INT else self.bool_term
            expr = conv(incoming[-1][1])
            for pred, val in reversed(incoming[:-1]):
                expr = z3.If(self.edge(pred, bid), conv(val), expr)
            return expr
        if op is Op.PHI and k is Kind.OPTIONAL and ins.type is not None and ins.type.inner in NUMERIC_EXACT and not ins.attrs.get("maybe_unbound"):
            # Value part of an optional join; a None incoming contributes a dummy that is
            # only meaningful together with none_term (always checked first).
            bid = self.def_block[ins.result]
            incoming = ins.attrs["incoming"]
            inner = ins.type.inner

            def conv(val: str) -> Any:
                if self.kind(val) is Kind.NONE:
                    return z3.IntVal(0) if inner is Kind.INT else z3.BoolVal(False)
                return self.int_term(val) if inner is Kind.INT else self.bool_term(val)

            expr = conv(incoming[-1][1])
            for pred, val in reversed(incoming[:-1]):
                expr = z3.If(self.edge(pred, bid), conv(val), expr)
            return expr
        return None

    # -- control flow ------------------------------------------------------------------
    def division_guard(self, ins: Instruction) -> Any:
        """Fact that holds after ``ins`` executes without ZeroDivisionError."""
        divisor = ins.operands[1]
        if ins.flavor in ("python.dynamic_dispatch", "python.str_format"):
            return None  # user-defined operator may accept a zero divisor (audit A-07); formatting divides nothing
        if self.kind(divisor) in NUMERIC_EXACT:
            return self.int_term(divisor) != 0
        return None

    def prefix(self, bid: str, upto: int | None) -> Any:
        """Reachability of the point before instruction index ``upto`` in block ``bid``."""
        z3 = self.z3
        conj = [self.reach(bid)]
        block = self.fn.blocks[bid]
        instrs = block.instructions if upto is None else block.instructions[:upto]
        for i, ins in enumerate(instrs):
            if ins.op in (Op.DIV, Op.FLOORDIV, Op.MOD) or ins.attrs.get("obligation_property"):
                # Execution continues past a checked operation only if it did not fail.
                g = self.obligation_ok(ins, bid, i)
                if g is not None:
                    conj.append(g)
            elif ins.op is Op.ASSIGN and ins.attrs.get("checked_read"):
                # Execution continues past a read only if the variable is bound.
                conj.append(self.bound(ins.operands[0]))
        return z3.And(*conj) if len(conj) > 1 else conj[0]

    def bound(self, v: str) -> Any:
        """Condition under which value ``v`` (possibly a maybe-unbound PHI) is bound."""
        z3 = self.z3
        ins = self.defs.get(v)
        if ins is None or ins.op is not Op.PHI or not ins.attrs.get("maybe_unbound"):
            return z3.BoolVal(True)
        key = ("bound", v)
        if key in self.reach_memo:
            return self.reach_memo[key]
        bid = self.def_block[v]
        alts = [z3.And(self.edge(pred, bid), self.bound(val)) for pred, val in ins.attrs["incoming"] if val is not None]
        r = z3.Or(*alts) if len(alts) > 1 else (alts[0] if alts else z3.BoolVal(False))
        self.reach_memo[key] = r
        return r

    def reach(self, bid: str) -> Any:
        if bid in self.reach_memo:
            return self.reach_memo[bid]
        z3 = self.z3
        block = self.fn.blocks[bid]
        if bid == self.fn.entry_id:
            r = z3.BoolVal(True)
        elif not block.preds:
            r = z3.BoolVal(False)
        else:
            edges = [self.edge(p, bid) for p in dict.fromkeys(block.preds)]
            r = edges[0] if len(edges) == 1 else z3.Or(*edges)
        self.reach_memo[bid] = r
        return r

    def edge(self, pred: str, succ: str) -> Any:
        z3 = self.z3
        exit_cond = self.prefix(pred, None)
        term = self.fn.blocks[pred].terminator
        assert term is not None
        if term.op is Op.JUMP:
            return exit_cond
        c = self.bool_term(term.operands[0])
        t, f = term.attrs["true_target"], term.attrs["false_target"]
        if t == succ and f == succ:
            return exit_cond
        return z3.And(exit_cond, c if t == succ else z3.Not(c))

    def upstream_unknown_effects(self, bid: str, upto: int) -> list[Instruction]:
        """UNKNOWN-effect instructions on any path from entry to the obligation point."""
        region = self.backward_region(bid)
        out = []
        for b in self.fn.topo_order():
            if b not in region:
                continue
            instrs = self.fn.blocks[b].instructions
            if b == bid:
                instrs = instrs[:upto]
            out.extend(i for i in instrs if i.effect is Effect.UNKNOWN)
        return out

    def backward_region(self, bid: str) -> set[str]:
        seen = {bid}
        stack = [bid]
        while stack:
            for p in self.fn.blocks[stack.pop()].preds:
                if p not in seen:
                    seen.add(p)
                    stack.append(p)
        return seen


class Z3Backend:
    def __init__(self, per_query_timeout_ms: int, rlimit: int = 0, seed: int = 0) -> None:
        self.z3 = importlib.import_module("z3")
        self.version = self.z3.get_version_string()
        self.timeout_ms = per_query_timeout_ms
        self.rlimit = rlimit
        self.seed = seed

    def new_context(self) -> Z3In:
        return Z3In(self.z3, self.z3.Context())

    def encoder(self, fn: Function, ctx: Z3In | None = None) -> FunctionEncoder:
        return FunctionEncoder(ctx if ctx is not None else Z3In(self.z3, self.z3.main_ctx()), fn)

    def check(self, enc: FunctionEncoder, constraints: list[Any], timeout_ms: int | None = None) -> SolverOutcome:
        z3 = enc.z3
        timeout = self.timeout_ms if timeout_ms is None else timeout_ms
        s = z3.Solver()
        s.set("timeout", max(1, int(timeout)))
        s.set("random_seed", self.seed)
        if self.rlimit:
            s.set("rlimit", self.rlimit)
        for c in constraints + enc.side:
            s.add(c)
        text = s.sexpr()
        digest = "sha256:" + hashlib.sha256(
            f"z3={self.version};timeout={timeout};rlimit={self.rlimit};seed={self.seed}\n{text}".encode()
        ).hexdigest()
        t0 = time.perf_counter()
        res = s.check()
        elapsed = (time.perf_counter() - t0) * 1000
        out = SolverOutcome(
            status=SolverStatus.UNKNOWN,
            query_digest=digest,
            elapsed_ms=round(elapsed, 3),
            timeout_ms=timeout,
            rlimit=self.rlimit or None,
            exact=not enc.havoc,
            havoc_values=list(enc.havoc),
        )
        if res == z3.sat:
            out.status = SolverStatus.SAT
            m = s.model()
            model: dict[str, Any] = {}
            for name, var in enc.params.items():
                val = m.eval(var, model_completion=True)
                model[name] = bool(z3.is_true(val)) if z3.is_bool(val) else val.as_long()
            for name, (var, kind) in enc.seq_params.items():
                n = m.eval(var, model_completion=True).as_long()
                if n > 64:
                    out.status = SolverStatus.UNKNOWN
                    out.reason = f"witness length {n} for '{name}' exceeds the materialization bound 64"
                    out.model = None
                    return out
                elems = [0] * max(0, n)
                model[name] = elems if kind is Kind.LIST else tuple(elems)
            for name, var in enc.none_params.items():
                if z3.is_true(m.eval(var, model_completion=True)):
                    model[name] = None
            out.model = model
        elif res == z3.unsat:
            out.status = SolverStatus.UNSAT
        else:
            reason = s.reason_unknown()
            out.reason = reason
            if reason == "timeout":
                out.status = SolverStatus.TIMEOUT
            elif self.rlimit and reason in ("canceled", "max. resource limit exceeded", "resource limits reached"):
                out.status = SolverStatus.RESOURCE_LIMIT
            else:
                out.status = SolverStatus.UNKNOWN
        return out
