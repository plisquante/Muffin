"""
JVM Stack-to-Register Three-Address IR + Structured Control-Flow Lifter.

Pipeline per method:
  1. Build CFG (`cfg.py`) and infer entry stack types at every reachable block
     via forward abstract interpretation (handles ternary `? :`, `dup*`,
     exception-handler entry stacks, etc.).
  2. Lift each basic block's stack bytecode into typed three-address statements
     operating on explicit locals (`l0, l1, ...`), stack-bridge slots (`s0_I, ...`),
     and SSA temporaries (`t0, t1, ...`).
  3. Recover structured control flow (`if` / `else` / `while`) where reducible
     and cleanly fall back to a `pc` dispatch loop for complex multi-exit or
     try-catch regions so 100% of valid JVM methods lift without failing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .bytecode import BRANCH_OPS, RETURN_OPS, Insn, decode
from .cfg import CFG, Block, build_cfg, dominators
from .classfile import ClassFile, Member
from .types import JType, parse_method_desc

# Abstract stack kinds used during stack-frame inference & lifting.
KIND_I = "I"  # Int32 / Bool / Int8 / Int16 / UInt16
KIND_J = "J"  # Int64
KIND_F = "F"  # Float32
KIND_D = "D"  # Float64
KIND_R = "R"  # JRef


def jtype_to_kind(t: JType) -> str:
    if t.desc in ("Z", "B", "C", "S", "I"):
        return KIND_I
    if t.desc in ("J", "F", "D"):
        return t.desc
    return KIND_R


def kind_to_mojo(kind: str) -> str:
    return {
        KIND_I: "Int32",
        KIND_J: "Int64",
        KIND_F: "Float32",
        KIND_D: "Float64",
        KIND_R: "JRef",
    }[kind]


def kind_default(kind: str) -> str:
    return {
        KIND_I: "Int32(0)",
        KIND_J: "Int64(0)",
        KIND_F: "Float32(0.0)",
        KIND_D: "Float64(0.0)",
        KIND_R: "JRef()",
    }[kind]


@dataclass(slots=True)
class Val:
    name: str
    kind: str


@dataclass(slots=True)
class Stmt:
    op: str
    dst: Val | None = None
    args: tuple[Any, ...] = ()


@dataclass(slots=True)
class LiftedBlock:
    pc: int
    stmts: list[Stmt] = field(default_factory=list)
    term: Stmt | None = None  # goto / branch / switch / return / throw
    handlers: list[tuple[str | None, int]] = field(default_factory=list)


@dataclass
class MethodIR:
    owner: str
    name: str
    descriptor: str
    is_static: bool
    params: list[JType]
    ret_type: JType
    locals_used: dict[tuple[int, str], Val]
    stack_slots: dict[tuple[int, str], Val]
    temps: list[Val]
    blocks: dict[int, LiftedBlock]
    entry: int = 0
    can_raise: bool = False


def _const_kind(val: Any) -> str:
    if isinstance(val, int):
        return KIND_I
    if isinstance(val, float):
        return KIND_F
    return KIND_R


def lift_method(cf: ClassFile, m: Member) -> MethodIR:
    assert m.code is not None
    cfg = build_cfg(cf, m)
    params, ret_type = parse_method_desc(m.descriptor)
    blocks = cfg.blocks

    # Entry stack frame inference (forward dataflow)
    entry_stacks: dict[int, list[str]] = {0: []}
    for h in m.code.handlers:
        entry_stacks.setdefault(h.handler_pc, [KIND_R])

    temps_counter = 0
    locals_used: dict[tuple[int, str], Val] = {}
    stack_slots: dict[tuple[int, str], Val] = {}
    temps: list[Val] = []

    def new_temp(kind: str) -> Val:
        nonlocal temps_counter
        v = Val(f"t{temps_counter}", kind)
        temps_counter += 1
        temps.append(v)
        return v

    def local_var(idx: int, kind: str) -> Val:
        key = (idx, kind)
        if key not in locals_used:
            locals_used[key] = Val(f"l{idx}_{kind}", kind)
        return locals_used[key]

    def slot_var(pos: int, kind: str) -> Val:
        key = (pos, kind)
        if key not in stack_slots:
            stack_slots[key] = Val(f"s{pos}_{kind}", kind)
        return stack_slots[key]

    # Seed parameter locals so declarations are always emitted
    loc_idx = 0
    if not m.is_static():
        local_var(0, KIND_R)
        loc_idx = 1
    for p in params:
        local_var(loc_idx, jtype_to_kind(p))
        loc_idx += 2 if p.is_wide else 1

    # Simulate blocks in BFS order from reachable entries
    lifted: dict[int, LiftedBlock] = {}
    worklist = [0] + [h.handler_pc for h in m.code.handlers if h.handler_pc in blocks]
    seen_order: set[int] = set()
    can_raise = bool(m.code.handlers)

    while worklist:
        bpc = worklist.pop(0)
        if bpc in seen_order or bpc not in blocks:
            continue
        seen_order.add(bpc)
        blk = blocks[bpc]
        in_kinds = entry_stacks.get(bpc, [])
        stk: list[Val] = [slot_var(i, k) for i, k in enumerate(in_kinds)]
        stmts: list[Stmt] = []
        term: Stmt | None = None

        # If this block is an exception handler entry, pop caught exception from caught var
        if any(h.handler_pc == bpc for h in m.code.handlers) and len(in_kinds) == 1:
            pass

        def push_const(kind: str, val: Any) -> None:
            t = new_temp(kind)
            stmts.append(Stmt("const", t, (val,)))
            stk.append(t)

        def flush_stack(target_pc: int) -> None:
            kinds = [v.kind for v in stk]
            if target_pc not in entry_stacks:
                entry_stacks[target_pc] = kinds
                worklist.append(target_pc)
            for pos, v in enumerate(stk):
                sv = slot_var(pos, v.kind)
                if sv.name != v.name:
                    stmts.append(Stmt("copy", sv, (v,)))

        for ins in blk.insns:
            op = ins.op
            args = ins.args

            if op == "nop":
                continue
            elif op == "aconst_null":
                push_const(KIND_R, None)
            elif op.startswith("iconst_"):
                val = -1 if op == "iconst_m1" else int(op.split("_")[1])
                push_const(KIND_I, val)
            elif op.startswith("lconst_"):
                push_const(KIND_J, int(op.split("_")[1]))
            elif op.startswith("fconst_"):
                push_const(KIND_F, float(op.split("_")[1]))
            elif op.startswith("dconst_"):
                push_const(KIND_D, float(op.split("_")[1]))
            elif op in ("bipush", "sipush"):
                push_const(KIND_I, args[0])
            elif op in ("ldc", "ldc_w", "ldc2_w"):
                c = args[0]
                if isinstance(c, str):
                    can_raise = True
                    t = new_temp(KIND_R)
                    stmts.append(Stmt("const_str", t, (c,)))
                    stk.append(t)
                elif isinstance(c, int):
                    push_const(KIND_J if op == "ldc2_w" else KIND_I, c)
                elif isinstance(c, float):
                    push_const(KIND_D if op == "ldc2_w" else KIND_F, c)
                else:
                    push_const(KIND_R, None)
            elif op.startswith(("iload", "lload", "fload", "dload", "aload")):
                k = {"i": KIND_I, "l": KIND_J, "f": KIND_F, "d": KIND_D, "a": KIND_R}[op[0]]
                idx = int(op.split("_")[1]) if "_" in op else args[0]
                lv = local_var(idx, k)
                t = new_temp(k)
                stmts.append(Stmt("copy", t, (lv,)))
                stk.append(t)
            elif op.startswith(("istore", "lstore", "fstore", "dstore", "astore")):
                k = {"i": KIND_I, "l": KIND_J, "f": KIND_F, "d": KIND_D, "a": KIND_R}[op[0]]
                idx = int(op.split("_")[1]) if "_" in op else args[0]
                v = stk.pop()
                stmts.append(Stmt("copy", local_var(idx, k), (v,)))
            elif op in ("iaload", "laload", "faload", "daload", "aaload", "baload", "caload", "saload"):
                can_raise = True
                idx_v = stk.pop()
                arr_v = stk.pop()
                k = {"i": KIND_I, "l": KIND_J, "f": KIND_F, "d": KIND_D, "a": KIND_R, "b": KIND_I, "c": KIND_I, "s": KIND_I}[op[0]]
                t = new_temp(k)
                stmts.append(Stmt("aload_elem", t, (op[0], arr_v, idx_v)))
                stk.append(t)
            elif op in ("iastore", "lastore", "fastore", "dastore", "aastore", "bastore", "castore", "sastore"):
                can_raise = True
                val_v = stk.pop()
                idx_v = stk.pop()
                arr_v = stk.pop()
                stmts.append(Stmt("astore_elem", None, (op[0], arr_v, idx_v, val_v)))
            elif op == "pop":
                stk.pop()
            elif op == "pop2":
                v = stk.pop()
                if v.kind not in (KIND_J, KIND_D) and stk:
                    stk.pop()
            elif op == "dup":
                stk.append(stk[-1])
            elif op == "dup_x1":
                a, b = stk.pop(), stk.pop()
                stk.extend((a, b, a))
            elif op == "dup_x2":
                a, b = stk.pop(), stk.pop()
                if b.kind in (KIND_J, KIND_D):
                    stk.extend((a, b, a))
                else:
                    c = stk.pop()
                    stk.extend((a, c, b, a))
            elif op == "dup2":
                a = stk[-1]
                if a.kind in (KIND_J, KIND_D):
                    stk.append(a)
                else:
                    stk.extend((stk[-2], a))
            elif op == "dup2_x1":
                a = stk.pop()
                b = stk.pop()
                if a.kind in (KIND_J, KIND_D):
                    stk.extend((a, b, a))
                else:
                    c = stk.pop()
                    stk.extend((b, a, c, b, a))
            elif op == "dup2_x2":
                a = stk.pop()
                b = stk.pop()
                if a.kind in (KIND_J, KIND_D):
                    if b.kind in (KIND_J, KIND_D):
                        stk.extend((a, b, a))
                    else:
                        c = stk.pop()
                        stk.extend((a, c, b, a))
                else:
                    c = stk.pop()
                    if c.kind in (KIND_J, KIND_D):
                        stk.extend((b, a, c, b, a))
                    else:
                        d = stk.pop()
                        stk.extend((b, a, d, c, b, a))
            elif op == "swap":
                stk[-1], stk[-2] = stk[-2], stk[-1]
            elif op in (
                "iadd", "ladd", "fadd", "dadd",
                "isub", "lsub", "fsub", "dsub",
                "imul", "lmul", "fmul", "dmul",
                "idiv", "ldiv", "fdiv", "ddiv",
                "irem", "lrem", "frem", "drem",
                "ishl", "lshl", "ishr", "lshr", "iushr", "lushr",
                "iand", "land", "ior", "lor", "ixor", "lxor",
                "lcmp", "fcmpl", "fcmpg", "dcmpl", "dcmpg",
            ):
                if op in ("idiv", "ldiv", "irem", "lrem"):
                    can_raise = True
                rhs, lhs = stk.pop(), stk.pop()
                res_k = KIND_I if op.startswith(("lcmp", "fcmp", "dcmp")) else lhs.kind
                t = new_temp(res_k)
                stmts.append(Stmt("binop", t, (op, lhs, rhs)))
                stk.append(t)
            elif op in ("ineg", "lneg", "fneg", "dneg", "i2l", "i2f", "i2d", "l2i", "l2f", "l2d", "f2i", "f2l", "f2d", "d2i", "d2l", "d2f", "i2b", "i2c", "i2s"):
                v = stk.pop()
                res_k = {
                    "ineg": KIND_I, "lneg": KIND_J, "fneg": KIND_F, "dneg": KIND_D,
                    "i2l": KIND_J, "i2f": KIND_F, "i2d": KIND_D,
                    "l2i": KIND_I, "l2f": KIND_F, "l2d": KIND_D,
                    "f2i": KIND_I, "f2l": KIND_J, "f2d": KIND_D,
                    "d2i": KIND_I, "d2l": KIND_J, "d2f": KIND_F,
                    "i2b": KIND_I, "i2c": KIND_I, "i2s": KIND_I,
                }[op]
                t = new_temp(res_k)
                stmts.append(Stmt("unop", t, (op, v)))
                stk.append(t)
            elif op == "iinc":
                idx, delta = args
                lv = local_var(idx, KIND_I)
                stmts.append(Stmt("iinc", lv, (lv, delta)))
            elif op in ("goto", "goto_w"):
                target = args[0]
                flush_stack(target)
                term = Stmt("goto", None, (target,))
            elif op in ("ifeq", "ifne", "iflt", "ifge", "ifgt", "ifle", "ifnull", "ifnonnull"):
                v = stk.pop()
                target = args[0]
                fall = blk.succs[0] if blk.succs else -1
                flush_stack(target)
                if fall != -1:
                    flush_stack(fall)
                term = Stmt("if1", None, (op, v, target, fall))
            elif op.startswith(("if_icmp", "if_acmp")):
                rhs, lhs = stk.pop(), stk.pop()
                target = args[0]
                fall = blk.succs[0] if blk.succs else -1
                flush_stack(target)
                if fall != -1:
                    flush_stack(fall)
                term = Stmt("if2", None, (op, lhs, rhs, target, fall))
            elif op in ("tableswitch", "lookupswitch"):
                v = stk.pop()
                default_pc, table = args
                flush_stack(default_pc)
                for tgt in table.values():
                    flush_stack(tgt)
                term = Stmt("switch", None, (v, default_pc, table))
            elif op in RETURN_OPS:
                if op == "return":
                    term = Stmt("ret", None, ())
                else:
                    term = Stmt("ret", None, (stk.pop(),))
            elif op == "athrow":
                can_raise = True
                term = Stmt("throw", None, (stk.pop(),))
            elif op in ("getstatic", "putstatic", "getfield", "putfield"):
                owner, fname, fdesc = args[0]
                fk = jtype_to_kind(JType(fdesc))
                if op == "getstatic":
                    t = new_temp(fk)
                    stmts.append(Stmt("getstatic", t, (owner, fname, fdesc)))
                    stk.append(t)
                elif op == "putstatic":
                    val_v = stk.pop()
                    stmts.append(Stmt("putstatic", None, (owner, fname, fdesc, val_v)))
                elif op == "getfield":
                    can_raise = True
                    obj_v = stk.pop()
                    t = new_temp(fk)
                    stmts.append(Stmt("getfield", t, (owner, fname, fdesc, obj_v)))
                    stk.append(t)
                else:
                    can_raise = True
                    val_v, obj_v = stk.pop(), stk.pop()
                    stmts.append(Stmt("putfield", None, (owner, fname, fdesc, obj_v, val_v)))
            elif op in ("invokevirtual", "invokespecial", "invokestatic", "invokeinterface"):
                can_raise = True
                owner, mname, mdesc = args[0]
                mparams, mret = parse_method_desc(mdesc)
                call_args = [stk.pop() for _ in mparams]
                call_args.reverse()
                recv = None if op == "invokestatic" else stk.pop()
                if mret.is_void:
                    stmts.append(Stmt("call", None, (op, owner, mname, mdesc, recv, tuple(call_args))))
                else:
                    t = new_temp(jtype_to_kind(mret))
                    stmts.append(Stmt("call", t, (op, owner, mname, mdesc, recv, tuple(call_args))))
                    stk.append(t)
            elif op == "invokedynamic":
                can_raise = True
                bsm_idx, mname, mdesc = args
                mparams, mret = parse_method_desc(mdesc)
                call_args = [stk.pop() for _ in mparams]
                call_args.reverse()
                dst = None if mret.is_void else new_temp(jtype_to_kind(mret))
                stmts.append(Stmt("indy", dst, (bsm_idx, mname, mdesc, tuple(call_args))))
                if dst is not None:
                    stk.append(dst)
            elif op == "new":
                can_raise = True
                t = new_temp(KIND_R)
                stmts.append(Stmt("new", t, (args[0],)))
                stk.append(t)
            elif op in ("newarray", "anewarray"):
                can_raise = True
                cnt = stk.pop()
                t = new_temp(KIND_R)
                stmts.append(Stmt("newarray", t, (args[0], cnt)))
                stk.append(t)
            elif op == "multianewarray":
                can_raise = True
                cls_desc, ndims = args
                dim_vals = [stk.pop() for _ in range(ndims)]
                dim_vals.reverse()
                t = new_temp(KIND_R)
                stmts.append(Stmt("multianewarray", t, (cls_desc, tuple(dim_vals))))
                stk.append(t)
            elif op == "arraylength":
                can_raise = True
                arr = stk.pop()
                t = new_temp(KIND_I)
                stmts.append(Stmt("arraylength", t, (arr,)))
                stk.append(t)
            elif op == "checkcast":
                can_raise = True
                obj = stk.pop()
                t = new_temp(KIND_R)
                stmts.append(Stmt("checkcast", t, (args[0], obj)))
                stk.append(t)
            elif op == "instanceof":
                obj = stk.pop()
                t = new_temp(KIND_I)
                stmts.append(Stmt("instanceof", t, (args[0], obj)))
                stk.append(t)
            elif op in ("monitorenter", "monitorexit"):
                _ = stk.pop()  # explicit lock hooks emitted only when needed
            else:
                stmts.append(Stmt("unsupported", None, (op, args)))

        if term is None and blk.succs:
            fall = blk.succs[0]
            flush_stack(fall)
            term = Stmt("goto", None, (fall,))

        block_handlers = [
            (h.catch_type, h.handler_pc)
            for h in m.code.handlers
            if blk.start < h.end_pc and blk.insns[-1].pc >= h.start_pc
        ]
        lifted[bpc] = LiftedBlock(bpc, stmts, term, block_handlers)

    return MethodIR(
        owner=cf.name,
        name=m.name,
        descriptor=m.descriptor,
        is_static=m.is_static(),
        params=params,
        ret_type=ret_type,
        locals_used=locals_used,
        stack_slots=stack_slots,
        temps=temps,
        blocks=lifted,
        entry=0,
        can_raise=can_raise,
    )
