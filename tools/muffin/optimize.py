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

from .cfg import CFG, build_cfg, dominators
from .ir import MethodIR, Stmt, Val


@dataclass
class OptInfo:
    arena_allocs: set[str] = field(default_factory=set)  # temp names safe for FrameArena
    live_vars: set[str] = field(default_factory=set)  # variable names actually referenced
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


def compute_non_escaping_methods(irs: list[MethodIR]) -> set[tuple[str, str, str]]:
    """Find methods whose receiver `arg0` (`l0_R`) never escapes to heap/return/unknown calls."""
    by_key = {(ir.owner, ir.name, ir.descriptor): ir for ir in irs}
    safe: set[tuple[str, str, str]] = {("java/lang/Object", "<init>", "()V")}

    changed = True
    while changed:
        changed = False
        for key, ir in by_key.items():
            if key in safe or ir.is_static:
                continue
            aliases: dict[str, set[str]] = {"l0_R": {"l0_R"}}
            escaped = False
            for blk in ir.blocks.values():
                for st in blk.stmts:
                    op, dst, a = st.op, st.dst, st.args
                    if op == "copy" and dst is not None:
                        if "l0_R" in aliases.get(a[0].name, ()):
                            aliases.setdefault(dst.name, set()).add("l0_R")
                    elif op == "checkcast" and dst is not None:
                        if "l0_R" in aliases.get(a[1].name, ()):
                            aliases.setdefault(dst.name, set()).add("l0_R")
                    elif op == "putstatic":
                        if "l0_R" in aliases.get(a[3].name, ()):
                            escaped = True
                    elif op == "putfield":
                        if "l0_R" in aliases.get(a[4].name, ()):
                            escaped = True
                    elif op == "astore_elem" and a[0] == "a":
                        if "l0_R" in aliases.get(a[3].name, ()):
                            escaped = True
                    elif op == "call":
                        _, own, mn, md, recv, cargs = a
                        if recv is not None and "l0_R" in aliases.get(recv.name, ()):
                            if (own, mn, md) not in safe:
                                escaped = True
                        for ca in cargs:
                            if "l0_R" in aliases.get(ca.name, ()):
                                escaped = True
                    elif op == "indy":
                        for ca in a[3]:
                            if "l0_R" in aliases.get(ca.name, ()):
                                escaped = True
                if blk.term is not None:
                    if blk.term.op == "ret" and blk.term.args:
                        if "l0_R" in aliases.get(blk.term.args[0].name, ()):
                            escaped = True
                    elif blk.term.op == "throw":
                        if "l0_R" in aliases.get(blk.term.args[0].name, ()):
                            escaped = True
            if not escaped:
                safe.add(key)
                changed = True
    return safe


def optimize_method(
    ir: MethodIR,
    *,
    non_escaping_recv: set[tuple[str, str, str]] | None = None,
) -> OptInfo:
    """Run copy propagation, dead-temp elimination, and escape analysis on `ir`."""
    safe_recv = non_escaping_recv or {("java/lang/Object", "<init>", "()V")}
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
    if not has_loop:
        alloc_sites: set[str] = set()
        aliases: dict[str, set[str]] = {}
        escaped_vars: set[str] = set()

        def origins(name: str) -> set[str]:
            return aliases.get(name, {name})

        for blk in ir.blocks.values():
            for st in blk.stmts:
                if st.op in ("new", "newarray") and st.dst is not None:
                    if st.op == "newarray" or st.args[0] != "java/lang/StringBuilder":
                        alloc_sites.add(st.dst.name)
                        aliases[st.dst.name] = {st.dst.name}
                elif st.op == "copy" and st.dst is not None:
                    aliases[st.dst.name] = set(origins(st.args[0].name))
                elif st.op == "checkcast" and st.dst is not None:
                    aliases[st.dst.name] = set(origins(st.args[1].name))
                elif st.op == "putstatic":
                    escaped_vars.update(origins(st.args[3].name))
                elif st.op == "putfield":
                    escaped_vars.update(origins(st.args[4].name))
                elif st.op == "astore_elem" and st.args[0] == "a":
                    escaped_vars.update(origins(st.args[3].name))
                elif st.op == "call":
                    cop, owner, mname, mdesc, recv, cargs = st.args
                    if recv is not None:
                        if (owner, mname, mdesc) not in safe_recv:
                            escaped_vars.update(origins(recv.name))
                    for ca in cargs:
                        escaped_vars.update(origins(ca.name))
                elif st.op == "indy":
                    for ca in st.args[3]:
                        escaped_vars.update(origins(ca.name))
            if blk.term is not None:
                if blk.term.op == "ret" and blk.term.args:
                    escaped_vars.update(origins(blk.term.args[0].name))
                elif blk.term.op == "throw":
                    escaped_vars.update(origins(blk.term.args[0].name))

        arena_allocs = alloc_sites - escaped_vars

    return OptInfo(arena_allocs=arena_allocs, live_vars=live)
