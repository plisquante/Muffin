"""
Structured Control-Flow Recovery (Dominator + Natural-Loop Structurer).

Recovers clean, idiomatic Mojo `if` / `else` / `while` / `break` / `continue`
ASTs from reducible JVM CFGs so LLVM/MLIR can keep induction variables in
CPU registers, apply LICM, and auto-vectorize inner loops.

For irregular multi-exit regions or methods with `try/catch` tables, falls back
cleanly to a `while True` `_pc` dispatch block while preserving 100% JVM semantics.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .cfg import CFG, _dominates, _rpo, build_cfg, dominators
from .ir import LiftedBlock, MethodIR, Stmt


@dataclass(slots=True)
class SeqNode:
    items: list[object] = field(default_factory=list)


@dataclass(slots=True)
class BlockNode:
    block: LiftedBlock
    emit_term: bool = True  # False when structured `If` / `While` handles branch


@dataclass(slots=True)
class IfNode:
    cond_stmt: Stmt  # if1 or if2
    then_node: object
    else_node: object


@dataclass(slots=True)
class WhileNode:
    body: object


@dataclass(slots=True)
class ContinueNode:
    pass


@dataclass(slots=True)
class BreakNode:
    pass


@dataclass(slots=True)
class DispatchNode:
    blocks: list[LiftedBlock]


def _compute_natural_loops(
    blocks: dict[int, LiftedBlock], idom: dict[int, int], succs: dict[int, list[int]], preds: dict[int, list[int]]
) -> dict[int, set[int]]:
    """Map loop_header_pc -> set of block PCs belonging to natural loops at that header."""
    loops: dict[int, set[int]] = {}
    for u, slist in succs.items():
        for h in slist:
            if h in idom and u in idom and _dominates(idom, h, u):
                body = loops.setdefault(h, {h})
                if u not in body:
                    body.add(u)
                    stk = [u]
                    while stk:
                        cur = stk.pop()
                        for p in preds.get(cur, ()):
                            if p not in body and p in idom:
                                body.add(p)
                                stk.append(p)
    return loops


def _postdominators(nodes: list[int], succs: dict[int, list[int]]) -> dict[int, int]:
    """Compute post-dominator tree using a virtual exit node (-1)."""
    exits = [n for n in nodes if not succs.get(n)]
    if not exits:
        return {}
    # Reverse postorder from virtual exit -1 along reversed edges
    rev_succ: dict[int, list[int]] = {-1: list(exits)}
    for n in nodes:
        for s in succs.get(n, ()):
            rev_succ.setdefault(s, []).append(n)

    seen: set[int] = {-1}
    rpo: list[int] = []
    stk: list[tuple[int, int]] = [(-1, 0)]
    while stk:
        u, i = stk[-1]
        nbrs = rev_succ.get(u, ())
        if i < len(nbrs):
            stk[-1] = (u, i + 1)
            v = nbrs[i]
            if v not in seen:
                seen.add(v)
                stk.append((v, 0))
        else:
            stk.pop()
            rpo.append(u)
    rpo.reverse()
    idx = {n: i for i, n in enumerate(rpo)}
    ipdom: dict[int, int] = {-1: -1}

    def intersect(a: int, b: int) -> int:
        while a != b:
            while idx[a] > idx[b]:
                a = ipdom[a]
            while idx[b] > idx[a]:
                b = ipdom[b]
        return a

    changed = True
    while changed:
        changed = False
        for n in rpo[1:]:
            outs = [s for s in (succs.get(n) or [-1]) if s in ipdom]
            if not outs:
                continue
            new = outs[0]
            for s in outs[1:]:
                new = intersect(s, new)
            if ipdom.get(n) != new:
                ipdom[n] = new
                changed = True
    return ipdom


def structure_method(ir: MethodIR) -> object:
    """Recover structured Mojo AST for `ir`, or return `DispatchNode` if complex."""
    blocks = ir.blocks
    if len(blocks) == 1:
        return BlockNode(blocks[ir.entry], emit_term=True)

    if any(b.handlers for b in blocks.values()):
        return DispatchNode([blocks[pc] for pc in sorted(blocks)])

    # Build successor/predecessor maps over lifted blocks
    succs: dict[int, list[int]] = {pc: [] for pc in blocks}
    preds: dict[int, list[int]] = {pc: [] for pc in blocks}
    for pc, blk in blocks.items():
        if blk.term is None:
            continue
        op, args = blk.term.op, blk.term.args
        tlist: list[int] = []
        if op == "goto":
            tlist = [args[0]]
        elif op == "if1":
            tlist = [args[3], args[2]]
        elif op == "if2":
            tlist = [args[4], args[3]]
        elif op == "switch":
            tlist = sorted({args[1], *args[2].values()})
        succs[pc] = [t for t in tlist if t in blocks]
        for t in succs[pc]:
            preds[t].append(pc)

    # Check if any switch exists -> DispatchNode
    if any(b.term is not None and b.term.op == "switch" for b in blocks.values()):
        return DispatchNode([blocks[pc] for pc in sorted(blocks)])

    # Dominators on lifted CFG
    fake_cfg = CFG(ir.entry, {pc: None for pc in blocks})  # type: ignore[arg-type]
    # Compute RPO + idom directly using succs
    seen: set[int] = {ir.entry}
    rpo: list[int] = []
    stk: list[tuple[int, int]] = [(ir.entry, 0)]
    while stk:
        u, i = stk[-1]
        sl = succs.get(u, ())
        if i < len(sl):
            stk[-1] = (u, i + 1)
            v = sl[i]
            if v not in seen:
                seen.add(v)
                stk.append((v, 0))
        else:
            stk.pop()
            rpo.append(u)
    rpo.reverse()
    rpo_idx = {n: i for i, n in enumerate(rpo)}
    idom: dict[int, int] = {ir.entry: ir.entry}

    def intersect(a: int, b: int) -> int:
        while a != b:
            while rpo_idx[a] > rpo_idx[b]:
                a = idom[a]
            while rpo_idx[b] > rpo_idx[a]:
                b = idom[b]
        return a

    changed = True
    while changed:
        changed = False
        for n in rpo[1:]:
            ps = [p for p in preds.get(n, ()) if p in idom]
            if not ps:
                continue
            new = ps[0]
            for p in ps[1:]:
                new = intersect(p, new)
            if idom.get(n) != new:
                idom[n] = new
                changed = True

    loops = _compute_natural_loops(blocks, idom, succs, preds)
    # Check reducibility
    for u in rpo:
        for s in succs.get(u, ()):
            if s in rpo_idx and rpo_idx[s] <= rpo_idx[u] and not _dominates(idom, s, u):
                return DispatchNode([blocks[pc] for pc in sorted(blocks)])

    # For acyclic post-dominator merge hints (computed on DAG without back-edges)
    dag_succs = {
        u: [s for s in sl if not (s in rpo_idx and rpo_idx[s] <= rpo_idx[u])]
        for u, sl in succs.items()
    }
    ipdom = _postdominators(rpo, dag_succs)

    class _Fail(Exception):
        pass

    def build_region(
        pc: int,
        stops: frozenset[int],
        loop_hdr: int | None,
        loop_exit: int | None,
        active_loops: frozenset[int],
        depth: int,
        entering_loop: bool = False,
    ) -> object:
        if depth > 128:
            raise _Fail()
        seq = SeqNode()
        cur: int | None = pc
        visited_seq: set[int] = set()

        first_step = True
        while cur is not None:
            if cur in stops:
                break
            if not (entering_loop and first_step) and cur == loop_hdr:
                seq.items.append(ContinueNode())
                break
            if cur == loop_exit:
                seq.items.append(BreakNode())
                break
            first_step = False
            if cur in visited_seq:
                raise _Fail()
            visited_seq.add(cur)

            # Check if `cur` starts a natural loop we haven't entered yet
            if cur in loops and cur not in active_loops:
                body_set = loops[cur]
                exits = sorted({s for b in body_set for s in succs[b] if s not in body_set})
                if len(exits) > 1:
                    raise _Fail()
                # Check single entry (no edge from outside body_set into body_set\{cur})
                for b in body_set:
                    if b == cur:
                        continue
                    if any(p not in body_set for p in preds.get(b, ())):
                        raise _Fail()
                ext = exits[0] if exits else -1
                body_ast = build_region(
                    cur,
                    stops,
                    cur,
                    ext if ext != -1 else None,
                    active_loops | frozenset({cur}),
                    depth + 1,
                    entering_loop=True,
                )
                seq.items.append(WhileNode(body_ast))
                if ext == -1 or ext in stops or ext == loop_exit:
                    if ext == loop_exit:
                        seq.items.append(BreakNode())
                    break
                cur = ext
                continue

            blk = blocks[cur]
            term = blk.term
            if term is None:
                seq.items.append(BlockNode(blk, emit_term=False))
                break
            if term.op in ("ret", "throw"):
                seq.items.append(BlockNode(blk, emit_term=True))
                break
            if term.op == "goto":
                seq.items.append(BlockNode(blk, emit_term=False))
                cur = term.args[0]
                continue
            if term.op in ("if1", "if2"):
                seq.items.append(BlockNode(blk, emit_term=False))
                tgt = term.args[2] if term.op == "if1" else term.args[3]
                fall = term.args[3] if term.op == "if1" else term.args[4]
                merge = ipdom.get(cur, -1)
                if merge == -1 or merge in stops:
                    merge_stop = stops
                    next_pc: int | None = None
                else:
                    merge_stop = stops | frozenset({merge})
                    next_pc = merge
                if merge == loop_hdr or merge == loop_exit:
                    next_pc = merge
                    merge_stop = stops | frozenset({merge})
                then_ast = build_region(tgt, merge_stop, loop_hdr, loop_exit, active_loops, depth + 1)
                else_ast = build_region(fall, merge_stop, loop_hdr, loop_exit, active_loops, depth + 1)
                seq.items.append(IfNode(term, then_ast, else_ast))
                cur = next_pc
                continue
            raise _Fail()

        if len(seq.items) == 1:
            return seq.items[0]
        return seq

    try:
        return build_region(ir.entry, frozenset(), None, None, frozenset(), 0)
    except _Fail:
        return DispatchNode([blocks[pc] for pc in sorted(blocks)])
