"""
Control-flow graph construction + reducibility analysis.

Why this matters for Mojo: Mojo has no `goto`. Every Java method must be
re-expressed with structured control flow (`if` / `while` / `break` /
`continue` / `try`). That is always possible for *reducible* CFGs; irreducible
ones need a fallback (a `while True` + state-variable dispatch loop).
`javac` almost never emits irreducible flow, and this module lets us *prove*
that for a given jar instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bytecode import BRANCH_OPS, RETURN_OPS, TERMINATORS, Insn, decode
from .classfile import ClassFile, Member


@dataclass(slots=True)
class Block:
    start: int
    insns: list[Insn] = field(default_factory=list)
    succs: list[int] = field(default_factory=list)  # normal edges (block start pcs)
    handlers: list[int] = field(default_factory=list)  # exceptional edges


@dataclass(slots=True)
class CFG:
    entry: int
    blocks: dict[int, Block]

    def all_succs(self, b: int) -> list[int]:
        blk = self.blocks[b]
        return blk.succs + blk.handlers


def build_cfg(cf: ClassFile, m: Member) -> CFG:
    assert m.code is not None
    insns = list(m.code.insns_override) if m.code.insns_override is not None else list(decode(m.code.code, cf.cp))
    leaders = {0}
    for h in m.code.handlers:
        leaders.update((h.start_pc, h.end_pc, h.handler_pc))
    for idx, ins in enumerate(insns):
        nxt = insns[idx + 1].pc if idx + 1 < len(insns) else None
        if ins.op in BRANCH_OPS:
            leaders.add(ins.args[0])
            if nxt is not None:
                leaders.add(nxt)
        elif ins.op in ("tableswitch", "lookupswitch"):
            leaders.add(ins.args[0])
            leaders.update(ins.args[1].values())
            if nxt is not None:
                leaders.add(nxt)
        elif ins.op in TERMINATORS and nxt is not None:
            leaders.add(nxt)

    blocks: dict[int, Block] = {}
    cur: Block | None = None
    for ins in insns:
        if ins.pc in leaders or cur is None:
            cur = Block(ins.pc)
            blocks[ins.pc] = cur
        cur.insns.append(ins)

    starts = sorted(blocks)
    for i, s in enumerate(starts):
        blk = blocks[s]
        last = blk.insns[-1]
        fall = starts[i + 1] if i + 1 < len(starts) else None
        op = last.op
        if op in ("goto", "goto_w"):
            blk.succs = [last.args[0]]
        elif op in ("tableswitch", "lookupswitch"):
            blk.succs = sorted({last.args[0], *last.args[1].values()})
        elif op in BRANCH_OPS:  # conditional (jsr/jsr_w do not occur in class v50+)
            blk.succs = [t for t in (fall, last.args[0]) if t is not None]
        elif op in RETURN_OPS or op in ("athrow", "ret"):
            blk.succs = []
        elif fall is not None:
            blk.succs = [fall]
        # exception edges: any block overlapping a protected range may jump to its handler
        end = blk.insns[-1].pc
        for h in m.code.handlers:
            if blk.start < h.end_pc and end >= h.start_pc:
                blk.handlers.append(h.handler_pc)
    return CFG(0, blocks)


def _rpo(cfg: CFG) -> list[int]:
    seen: set[int] = set()
    order: list[int] = []
    stack: list[tuple[int, int]] = [(cfg.entry, 0)]
    seen.add(cfg.entry)
    while stack:
        b, i = stack[-1]
        succ = cfg.all_succs(b)
        if i < len(succ):
            stack[-1] = (b, i + 1)
            s = succ[i]
            if s not in seen:
                seen.add(s)
                stack.append((s, 0))
        else:
            stack.pop()
            order.append(b)
    order.reverse()
    return order


def dominators(cfg: CFG) -> dict[int, int]:
    """Cooper-Harvey-Kennedy iterative dominator algorithm. Returns idom map."""
    rpo = _rpo(cfg)
    index = {b: i for i, b in enumerate(rpo)}
    preds: dict[int, list[int]] = {b: [] for b in rpo}
    for b in rpo:
        for s in cfg.all_succs(b):
            if s in preds:
                preds[s].append(b)
    idom: dict[int, int] = {cfg.entry: cfg.entry}

    def intersect(a: int, b: int) -> int:
        while a != b:
            while index[a] > index[b]:
                a = idom[a]
            while index[b] > index[a]:
                b = idom[b]
        return a

    changed = True
    while changed:
        changed = False
        for b in rpo[1:]:
            ps = [p for p in preds[b] if p in idom]
            if not ps:
                continue
            new = ps[0]
            for p in ps[1:]:
                new = intersect(p, new)
            if idom.get(b) != new:
                idom[b] = new
                changed = True
    return idom


def _dominates(idom: dict[int, int], a: int, b: int) -> bool:
    while True:
        if a == b:
            return True
        nb = idom.get(b)
        if nb is None or nb == b:
            return False
        b = nb


def is_reducible(cfg: CFG) -> bool:
    """A CFG is reducible iff every DFS retreating edge targets a dominator."""
    idom = dominators(cfg)
    rpo = _rpo(cfg)
    index = {b: i for i, b in enumerate(rpo)}
    for b in rpo:
        for s in cfg.all_succs(b):
            if s in index and index[s] <= index[b] and not _dominates(idom, s, b):
                return False
    return True
