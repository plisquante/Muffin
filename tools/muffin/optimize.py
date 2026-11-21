"""
Muffin Optimization Passes over MethodIR:
  1. Dead-code & unused-variable elimination (removes unused temps/slots/locals).
  2. Intra-block copy propagation (eliminates redundant temp->temp->local chains).
  3. Intra-method Escape Analysis: identifies non-escaping `new` / `newarray`
     allocations that can be bump-allocated on `FrameArena` with 0 malloc and
     0 atomic refcounting.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cfg import _dominates
from .ir import MethodIR, Stmt, Val


@dataclass
class OptInfo:
    arena_allocs: set[str] = field(default_factory=set)  # temp names safe for FrameArena
    live_vars: set[str] = field(default_factory=set)  # variable names actually referenced
    has_loop: bool = False
    inline_candidates: bool = False


def _stmt_reads(st: Stmt) -> list[Val]:
    op, args = st.op, st.args
    out: list[Val] = []
    if op == "copy":
        out.append(args[0])
    elif op == "iinc":
        out.append(args[0])
    elif op == "binop":
        out.extend((args[1], args[2]))
    elif op == "unop":
        out.append(args[1])
    elif op == "newarray":
        out.append(args[1])
    elif op == "multianewarray":
        out.extend(args[1])
    elif op == "arraylength":
        out.append(args[0])
    elif op == "aload_elem":
        out.extend((args[1], args[2]))
    elif op == "astore_elem":
        out.extend((args[1], args[2], args[3]))
    elif op == "putstatic":
        out.append(args[3])
    elif op == "getfield":
        out.append(args[3])
    elif op == "putfield":
        out.extend((args[3], args[4]))
    elif op in ("instanceof", "checkcast"):
        out.append(args[1])
    elif op == "call":
        recv, cargs = args[4], args[5]
        if recv is not None:
            out.append(recv)
        out.extend(cargs)
    elif op == "indy":
        out.extend(args[3])
    elif op == "if1":
        out.append(args[1])
    elif op == "if2":
        out.extend((args[1], args[2]))
    elif op == "switch":
        out.append(args[0])
    elif op == "ret" and args:
        out.append(args[0])
    elif op == "throw":
        out.append(args[0])
    return out


def _replace_reads(st: Stmt, repl: dict[str, Val]) -> None:
    def sub(v: Val) -> Val:
        while v.name in repl:
            nxt = repl[v.name]
            if nxt.name == v.name:
                break
            v = nxt
        return v

    op, a = st.op, st.args
    if op == "copy":
        st.args = (sub(a[0]),)
    elif op == "binop":
        st.args = (a[0], sub(a[1]), sub(a[2]))
    elif op == "unop":
        st.args = (a[0], sub(a[1]))
    elif op == "newarray":
        st.args = (a[0], sub(a[1]))
    elif op == "multianewarray":
        st.args = (a[0], tuple(sub(x) for x in a[1]))
    elif op == "arraylength":
        st.args = (sub(a[0]),)
    elif op == "aload_elem":
        st.args = (a[0], sub(a[1]), sub(a[2]))
    elif op == "astore_elem":
        st.args = (a[0], sub(a[1]), sub(a[2]), sub(a[3]))
    elif op == "putstatic":
        st.args = (a[0], a[1], a[2], sub(a[3]))
    elif op == "getfield":
        st.args = (a[0], a[1], a[2], sub(a[3]))
    elif op == "putfield":
        st.args = (a[0], a[1], a[2], sub(a[3]), sub(a[4]))
    elif op in ("instanceof", "checkcast"):
        st.args = (a[0], sub(a[1]))
    elif op == "call":
        recv = sub(a[4]) if a[4] is not None else None
        cargs = tuple(sub(x) for x in a[5])
        st.args = (a[0], a[1], a[2], a[3], recv, cargs)
    elif op == "indy":
        st.args = (a[0], a[1], a[2], tuple(sub(x) for x in a[3]))
    elif op == "if1":
        st.args = (a[0], sub(a[1]), a[2], a[3])
    elif op == "if2":
        st.args = (a[0], sub(a[1]), sub(a[2]), a[3], a[4])
    elif op == "switch":
        st.args = (sub(a[0]), a[1], a[2])
    elif op == "ret" and a:
        st.args = (sub(a[0]),)
    elif op == "throw":
        st.args = (sub(a[0]),)


def _has_side_effect(st: Stmt) -> bool:
    return st.op not in ("const", "const_str", "copy", "unop") and not (
        st.op == "binop" and st.args[0] not in ("idiv", "ldiv", "irem", "lrem")
    )


def compute_non_escaping_params(irs: list[MethodIR]) -> dict[tuple[str, str, str], set[int]]:
    """Find parameter positions (0=receiver, 1..N=args) per method that never escape."""
    from collections import defaultdict, deque

    from .ir import KIND_R, jtype_to_kind

    by_key = {(ir.owner, ir.name, ir.descriptor): ir for ir in irs}
    obj_init = ("java/lang/Object", "<init>", "()V")

    param_slots: dict[tuple[str, str, str], list[tuple[int, str]]] = {}
    for key, ir in by_key.items():
        slots: list[tuple[int, str]] = []
        loc = 0
        if not ir.is_static:
            slots.append((0, "l0_R"))
            loc = 1
        arg_pos = loc
        for pt in ir.params:
            if jtype_to_kind(pt) == KIND_R:
                slots.append((arg_pos, f"l{loc}_R"))
            arg_pos += 1
            loc += 2 if pt.is_wide else 1
        param_slots[key] = slots

    rev_edges: dict[tuple[tuple[str, str, str], int], list[tuple[tuple[str, str, str], int]]] = defaultdict(list)
    unsafe_mask: dict[tuple[str, str, str], int] = {}
    q: deque[tuple[tuple[str, str, str], int]] = deque()

    def _enqueue_bits(mkey: tuple[str, str, str], bits: int) -> None:
        b = bits
        while b:
            lsb = b & -b
            q.append((mkey, lsb.bit_length() - 1))
            b ^= lsb

    for key, ir in by_key.items():
        slots = param_slots[key]
        if not slots:
            continue
        taint: dict[str, int] = {lname: (1 << apos) for apos, lname in slots}
        local_changed = True
        while local_changed:
            local_changed = False
            for blk in ir.blocks.values():
                for st in blk.stmts:
                    op, dst, a = st.op, st.dst, st.args
                    if dst is None:
                        continue
                    if op == "copy":
                        new_m = taint.get(a[0].name, 0)
                    elif op == "checkcast":
                        new_m = taint.get(a[1].name, 0)
                    else:
                        continue
                    if new_m:
                        prev = taint.get(dst.name, 0)
                        merged = prev | new_m
                        if merged != prev:
                            taint[dst.name] = merged
                            local_changed = True

        always_escape = 0
        for blk in ir.blocks.values():
            for st in blk.stmts:
                op, a = st.op, st.args
                if op == "putstatic":
                    always_escape |= taint.get(a[3].name, 0)
                elif op == "putfield":
                    always_escape |= taint.get(a[4].name, 0)
                elif op == "astore_elem" and a[0] == "a":
                    always_escape |= taint.get(a[3].name, 0)
                elif op == "call":
                    cop, own, mn, md, recv, cargs = a
                    ckey = (own, mn, md)
                    callee_in_world = ckey in by_key or ckey == obj_init
                    if recv is not None:
                        rm = taint.get(recv.name, 0)
                        if rm:
                            if not callee_in_world:
                                always_escape |= rm
                            elif ckey != obj_init:
                                rev_edges[(ckey, 0)].append((key, rm))
                    off = 0 if cop == "invokestatic" else 1
                    for idx, ca in enumerate(cargs):
                        cm = taint.get(ca.name, 0)
                        if cm:
                            if not callee_in_world:
                                always_escape |= cm
                            elif ckey != obj_init:
                                rev_edges[(ckey, off + idx)].append((key, cm))
                elif op == "indy":
                    for ca in a[3]:
                        always_escape |= taint.get(ca.name, 0)
            if blk.term is not None:
                if blk.term.op == "ret" and blk.term.args:
                    always_escape |= taint.get(blk.term.args[0].name, 0)
                elif blk.term.op == "throw":
                    always_escape |= taint.get(blk.term.args[0].name, 0)

        if always_escape:
            unsafe_mask[key] = always_escape
            _enqueue_bits(key, always_escape)

    while q:
        ckey, capos = q.popleft()
        for caller_key, caller_mask in rev_edges.get((ckey, capos), ()):
            prev_u = unsafe_mask.get(caller_key, 0)
            delta = caller_mask & ~prev_u
            if delta:
                unsafe_mask[caller_key] = prev_u | delta
                _enqueue_bits(caller_key, delta)

    safe: dict[tuple[str, str, str], set[int]] = {obj_init: {0}}
    for key, slots in param_slots.items():
        if slots:
            um = unsafe_mask.get(key, 0)
            safe[key] = {apos for apos, _ in slots if not (um & (1 << apos))}
    return safe


def compute_non_escaping_methods(irs: list[MethodIR]) -> set[tuple[str, str, str]]:
    """Backward-compatible wrapper returning methods whose receiver `arg0` never escapes."""
    return {k for k, ps in compute_non_escaping_params(irs).items() if 0 in ps}


def optimize_method(
    ir: MethodIR,
    *,
    non_escaping_recv: set[tuple[str, str, str]] | None = None,
    non_escaping_params: dict[tuple[str, str, str], set[int]] | None = None,
) -> OptInfo:
    """Run copy propagation, dead-temp elimination, and escape analysis on `ir`."""
    if non_escaping_params is not None:
        safe_params = non_escaping_params
    elif non_escaping_recv is not None:
        safe_params = {k: {0} for k in non_escaping_recv}
    else:
        safe_params = {("java/lang/Object", "<init>", "()V"): {0}}
    # 1. Intra-block copy propagation for single-use temps `t*`
    for blk in ir.blocks.values():
        copy_map: dict[str, Val] = {}
        for st in blk.stmts:
            _replace_reads(st, copy_map)
            if st.dst is not None:
                dname = st.dst.name
                for k in [k for k, v in copy_map.items() if k == dname or v.name == dname]:
                    del copy_map[k]
                if st.op == "copy" and dname.startswith("t"):
                    src: Val = st.args[0]
                    if src.name != dname:
                        copy_map[dname] = src
        if blk.term is not None:
            _replace_reads(blk.term, copy_map)

    # 2. Iterative dead pure-assignment elimination
    changed = True
    while changed:
        changed = False
        reads: set[str] = set()
        for blk in ir.blocks.values():
            for st in blk.stmts:
                for v in _stmt_reads(st):
                    reads.add(v.name)
            if blk.term is not None:
                for v in _stmt_reads(blk.term):
                    reads.add(v.name)
        for blk in ir.blocks.values():
            new_stmts: list[Stmt] = []
            for st in blk.stmts:
                if st.dst is not None and st.dst.name not in reads and not _has_side_effect(st):
                    changed = True
                    continue
                new_stmts.append(st)
            blk.stmts = new_stmts

    # 3. Collect all actually used/written variable names
    live: set[str] = set()
    for blk in ir.blocks.values():
        for st in blk.stmts:
            if st.dst is not None:
                live.add(st.dst.name)
            for v in _stmt_reads(st):
                live.add(v.name)
        if blk.term is not None:
            for v in _stmt_reads(blk.term):
                live.add(v.name)

    # 4. Intra-method Escape Analysis
    has_loop = False
    for bpc, blk in ir.blocks.items():
        if blk.term is not None:
            targets = []
            if blk.term.op == "goto":
                targets = [blk.term.args[0]]
            elif blk.term.op == "if1":
                targets = [blk.term.args[2], blk.term.args[3]]
            elif blk.term.op == "if2":
                targets = [blk.term.args[3], blk.term.args[4]]
            elif blk.term.op == "switch":
                targets = [blk.term.args[1], *blk.term.args[2].values()]
            if any(t <= bpc for t in targets if t >= 0):
                has_loop = True
                break

    arena_allocs: set[str] = set()
    # Global intra-method EA (safe when loop-free) OR CFG-dominance EA (safe across loop branches)
    alloc_sites: set[str] = set()
    alloc_block: dict[str, int] = {}
    alloc_stmt_idx: dict[str, int] = {}
    aliases: dict[str, set[str]] = {}
    alias_vars: dict[str, set[str]] = {}
    write_blocks: dict[str, set[int]] = {}
    first_read_in_blk: dict[tuple[int, str], int] = {}
    write_in_blk: dict[tuple[int, str], int] = {}
    escaped_vars: set[str] = set()
    alias_blocks: dict[str, set[int]] = {}

    def origins(name: str) -> set[str]:
        return aliases.get(name, {name})

    def record_use(v: Val, bpc: int, stmt_i: int) -> None:
        if (bpc, v.name) not in first_read_in_blk:
            first_read_in_blk[(bpc, v.name)] = stmt_i
        for org in origins(v.name):
            alias_blocks.setdefault(org, set()).add(bpc)

    def record_write(dst_name: str, bpc: int, stmt_i: int) -> None:
        write_blocks.setdefault(dst_name, set()).add(bpc)
        if (bpc, dst_name) not in write_in_blk:
            write_in_blk[(bpc, dst_name)] = stmt_i

    for bpc, blk in ir.blocks.items():
        for si, st in enumerate(blk.stmts):
            for v in _stmt_reads(st):
                record_use(v, bpc, si)
            if st.op in ("new", "newarray") and st.dst is not None:
                if st.op == "newarray" or st.args[0] != "java/lang/StringBuilder":
                    alloc_sites.add(st.dst.name)
                    alloc_block[st.dst.name] = bpc
                    alloc_stmt_idx[st.dst.name] = si
                    aliases[st.dst.name] = {st.dst.name}
                    alias_vars[st.dst.name] = {st.dst.name}
                    alias_blocks.setdefault(st.dst.name, set()).add(bpc)
                    record_write(st.dst.name, bpc, si)
            elif st.op == "copy" and st.dst is not None:
                orgs = set(origins(st.args[0].name))
                aliases[st.dst.name] = orgs
                record_write(st.dst.name, bpc, si)
                for org in orgs:
                    alias_blocks.setdefault(org, set()).add(bpc)
                    if org in alias_vars:
                        alias_vars[org].add(st.dst.name)
            elif st.op == "checkcast" and st.dst is not None:
                orgs = set(origins(st.args[1].name))
                aliases[st.dst.name] = orgs
                record_write(st.dst.name, bpc, si)
                for org in orgs:
                    alias_blocks.setdefault(org, set()).add(bpc)
                    if org in alias_vars:
                        alias_vars[org].add(st.dst.name)
            elif st.op == "putstatic":
                escaped_vars.update(origins(st.args[3].name))
            elif st.op == "putfield":
                escaped_vars.update(origins(st.args[4].name))
            elif st.op == "astore_elem" and st.args[0] == "a":
                escaped_vars.update(origins(st.args[3].name))
            elif st.op == "call":
                cop, owner, mname, mdesc, recv, cargs = st.args
                csafe = safe_params.get((owner, mname, mdesc), ())
                if recv is not None and 0 not in csafe:
                    escaped_vars.update(origins(recv.name))
                off = 0 if cop == "invokestatic" else 1
                for idx, ca in enumerate(cargs):
                    if (off + idx) not in csafe:
                        escaped_vars.update(origins(ca.name))
                if st.dst is not None:
                    record_write(st.dst.name, bpc, si)
            elif st.op == "indy":
                for ca in st.args[3]:
                    escaped_vars.update(origins(ca.name))
                if st.dst is not None:
                    record_write(st.dst.name, bpc, si)
            elif st.dst is not None:
                record_write(st.dst.name, bpc, si)
        if blk.term is not None:
            for v in _stmt_reads(blk.term):
                record_use(v, bpc, len(blk.stmts))
            if blk.term.op == "ret" and blk.term.args:
                escaped_vars.update(origins(blk.term.args[0].name))
            elif blk.term.op == "throw":
                escaped_vars.update(origins(blk.term.args[0].name))

    candidates = alloc_sites - escaped_vars
    if not has_loop:
        arena_allocs = candidates
    else:
        idom = _ir_idom(ir)
        arena_allocs = set()
        for s in candidates:
            ab = alloc_block[s]
            ublks = alias_blocks.get(s, set())
            if not ublks or ab not in idom or not all(u in idom and _dominates(idom, ab, u) for u in ublks):
                continue
            # Ensure every alias variable holding `s` is unconditionally defined inside `ab`
            # before any read of that variable in `ab`.
            safe_vars = True
            for vn in alias_vars.get(s, ()):
                wblks = write_blocks.get(vn, set())
                if wblks != {ab}:
                    safe_vars = False
                    break
                widx = write_in_blk.get((ab, vn), -1)
                ridx = first_read_in_blk.get((ab, vn), 1_000_000)
                if widx < 0 or ridx < widx:
                    safe_vars = False
                    break
            if safe_vars:
                arena_allocs.add(s)

    return OptInfo(arena_allocs=arena_allocs, live_vars=live, has_loop=has_loop)


def _ir_idom(ir: MethodIR) -> dict[int, int]:
    """Compute immediate dominators directly over lifted `MethodIR.blocks`."""
    blocks = ir.blocks
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
    return idom
