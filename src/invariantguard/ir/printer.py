"""Human-readable IR dump in the style of docs/04 (for debugging and tests)."""

from __future__ import annotations

from .model import Function, Instruction, Op


def _fmt(ins: Instruction) -> str:
    parts = []
    if ins.result:
        parts.append(f"{ins.result} =")
    parts.append(ins.op.value)
    if ins.op is Op.CONST:
        parts.append(repr(ins.attrs.get("value")))
    elif ins.op is Op.PARAM:
        parts.append(ins.attrs["name"])
    elif ins.op is Op.COMPARE:
        parts.append(ins.attrs["cmp"])
    elif ins.op is Op.PHI:
        parts.append(", ".join(f"[{p}: {v or 'UNBOUND'}]" for p, v in ins.attrs["incoming"]))
    elif ins.op is Op.CALL:
        parts.append(ins.attrs.get("callee", "?"))
    if ins.operands and ins.op is not Op.PHI:
        parts.append(" ".join(ins.operands))
    if ins.op is Op.BRANCH:
        parts.append(f"{ins.attrs['true_target']} {ins.attrs['false_target']}")
    if ins.op is Op.JUMP:
        parts.append(ins.attrs["target"])
    if ins.op is Op.UNKNOWN_EFFECT:
        parts.append(f"({ins.attrs.get('reason')})")
    if ins.flavor:
        parts.append(f"[{ins.flavor}]")
    return " ".join(parts)


def format_function(fn: Function) -> str:
    lines = [f"function {fn.symbol_id}({', '.join(p.name for p in fn.params)})"]
    for bid in fn.topo_order():
        b = fn.blocks[bid]
        preds = f"  ; preds: {', '.join(b.preds)}" if b.preds else ""
        lines.append(f"{b.block_id} ({b.label}):{preds}")
        for ins in b.instructions:
            lines.append("  " + _fmt(ins))
        if b.terminator is not None:
            lines.append("  " + _fmt(b.terminator))
    return "\n".join(lines)
