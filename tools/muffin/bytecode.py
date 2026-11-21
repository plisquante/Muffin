"""
JVM bytecode decoder.

Turns a method's raw `code` bytes into a list of `Insn` objects with resolved
operands. This is the input for control-flow-graph construction and the
stack -> SSA lifting pass (see docs/ARCHITECTURE.md).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Iterator

from .classfile import ConstantPool

# Operand formats:
#   ""   no operand          "b"  s1      "B"  u1 (local idx)
#   "s"  s2                  "c"  u2 cp index        "C" u1 cp index (ldc)
#   "j"  s2 branch           "J"  s4 branch
#   "i"  iinc (u1 idx, s1)   "m"  multianewarray (u2 cp, u1 dims)
#   "I"  invokeinterface (u2 cp, u1 count, u1 0)
#   "D"  invokedynamic (u2 cp, u2 0)
#   "T"  tableswitch         "L"  lookupswitch      "W" wide prefix
#   "a"  newarray (u1 atype)
OPCODES: dict[int, tuple[str, str]] = {
    0x00: ("nop", ""), 0x01: ("aconst_null", ""),
    0x02: ("iconst_m1", ""), 0x03: ("iconst_0", ""), 0x04: ("iconst_1", ""),
    0x05: ("iconst_2", ""), 0x06: ("iconst_3", ""), 0x07: ("iconst_4", ""),
    0x08: ("iconst_5", ""), 0x09: ("lconst_0", ""), 0x0A: ("lconst_1", ""),
    0x0B: ("fconst_0", ""), 0x0C: ("fconst_1", ""), 0x0D: ("fconst_2", ""),
    0x0E: ("dconst_0", ""), 0x0F: ("dconst_1", ""),
    0x10: ("bipush", "b"), 0x11: ("sipush", "s"),
    0x12: ("ldc", "C"), 0x13: ("ldc_w", "c"), 0x14: ("ldc2_w", "c"),
    0x15: ("iload", "B"), 0x16: ("lload", "B"), 0x17: ("fload", "B"),
    0x18: ("dload", "B"), 0x19: ("aload", "B"),
    **{0x1A + i: (f"iload_{i}", "") for i in range(4)},
    **{0x1E + i: (f"lload_{i}", "") for i in range(4)},
    **{0x22 + i: (f"fload_{i}", "") for i in range(4)},
    **{0x26 + i: (f"dload_{i}", "") for i in range(4)},
    **{0x2A + i: (f"aload_{i}", "") for i in range(4)},
    0x2E: ("iaload", ""), 0x2F: ("laload", ""), 0x30: ("faload", ""),
    0x31: ("daload", ""), 0x32: ("aaload", ""), 0x33: ("baload", ""),
    0x34: ("caload", ""), 0x35: ("saload", ""),
    0x36: ("istore", "B"), 0x37: ("lstore", "B"), 0x38: ("fstore", "B"),
    0x39: ("dstore", "B"), 0x3A: ("astore", "B"),
    **{0x3B + i: (f"istore_{i}", "") for i in range(4)},
    **{0x3F + i: (f"lstore_{i}", "") for i in range(4)},
    **{0x43 + i: (f"fstore_{i}", "") for i in range(4)},
    **{0x47 + i: (f"dstore_{i}", "") for i in range(4)},
    **{0x4B + i: (f"astore_{i}", "") for i in range(4)},
    0x4F: ("iastore", ""), 0x50: ("lastore", ""), 0x51: ("fastore", ""),
    0x52: ("dastore", ""), 0x53: ("aastore", ""), 0x54: ("bastore", ""),
    0x55: ("castore", ""), 0x56: ("sastore", ""),
    0x57: ("pop", ""), 0x58: ("pop2", ""), 0x59: ("dup", ""),
    0x5A: ("dup_x1", ""), 0x5B: ("dup_x2", ""), 0x5C: ("dup2", ""),
    0x5D: ("dup2_x1", ""), 0x5E: ("dup2_x2", ""), 0x5F: ("swap", ""),
    0x60: ("iadd", ""), 0x61: ("ladd", ""), 0x62: ("fadd", ""), 0x63: ("dadd", ""),
    0x64: ("isub", ""), 0x65: ("lsub", ""), 0x66: ("fsub", ""), 0x67: ("dsub", ""),
    0x68: ("imul", ""), 0x69: ("lmul", ""), 0x6A: ("fmul", ""), 0x6B: ("dmul", ""),
    0x6C: ("idiv", ""), 0x6D: ("ldiv", ""), 0x6E: ("fdiv", ""), 0x6F: ("ddiv", ""),
    0x70: ("irem", ""), 0x71: ("lrem", ""), 0x72: ("frem", ""), 0x73: ("drem", ""),
    0x74: ("ineg", ""), 0x75: ("lneg", ""), 0x76: ("fneg", ""), 0x77: ("dneg", ""),
    0x78: ("ishl", ""), 0x79: ("lshl", ""), 0x7A: ("ishr", ""), 0x7B: ("lshr", ""),
    0x7C: ("iushr", ""), 0x7D: ("lushr", ""),
    0x7E: ("iand", ""), 0x7F: ("land", ""), 0x80: ("ior", ""), 0x81: ("lor", ""),
    0x82: ("ixor", ""), 0x83: ("lxor", ""), 0x84: ("iinc", "i"),
    0x85: ("i2l", ""), 0x86: ("i2f", ""), 0x87: ("i2d", ""), 0x88: ("l2i", ""),
    0x89: ("l2f", ""), 0x8A: ("l2d", ""), 0x8B: ("f2i", ""), 0x8C: ("f2l", ""),
    0x8D: ("f2d", ""), 0x8E: ("d2i", ""), 0x8F: ("d2l", ""), 0x90: ("d2f", ""),
    0x91: ("i2b", ""), 0x92: ("i2c", ""), 0x93: ("i2s", ""),
    0x94: ("lcmp", ""), 0x95: ("fcmpl", ""), 0x96: ("fcmpg", ""),
    0x97: ("dcmpl", ""), 0x98: ("dcmpg", ""),
    0x99: ("ifeq", "j"), 0x9A: ("ifne", "j"), 0x9B: ("iflt", "j"),
    0x9C: ("ifge", "j"), 0x9D: ("ifgt", "j"), 0x9E: ("ifle", "j"),
    0x9F: ("if_icmpeq", "j"), 0xA0: ("if_icmpne", "j"), 0xA1: ("if_icmplt", "j"),
    0xA2: ("if_icmpge", "j"), 0xA3: ("if_icmpgt", "j"), 0xA4: ("if_icmple", "j"),
    0xA5: ("if_acmpeq", "j"), 0xA6: ("if_acmpne", "j"),
    0xA7: ("goto", "j"), 0xA8: ("jsr", "j"), 0xA9: ("ret", "B"),
    0xAA: ("tableswitch", "T"), 0xAB: ("lookupswitch", "L"),
    0xAC: ("ireturn", ""), 0xAD: ("lreturn", ""), 0xAE: ("freturn", ""),
    0xAF: ("dreturn", ""), 0xB0: ("areturn", ""), 0xB1: ("return", ""),
    0xB2: ("getstatic", "c"), 0xB3: ("putstatic", "c"),
    0xB4: ("getfield", "c"), 0xB5: ("putfield", "c"),
    0xB6: ("invokevirtual", "c"), 0xB7: ("invokespecial", "c"),
    0xB8: ("invokestatic", "c"), 0xB9: ("invokeinterface", "I"),
    0xBA: ("invokedynamic", "D"),
    0xBB: ("new", "c"), 0xBC: ("newarray", "a"), 0xBD: ("anewarray", "c"),
    0xBE: ("arraylength", ""), 0xBF: ("athrow", ""),
    0xC0: ("checkcast", "c"), 0xC1: ("instanceof", "c"),
    0xC2: ("monitorenter", ""), 0xC3: ("monitorexit", ""),
    0xC4: ("wide", "W"), 0xC5: ("multianewarray", "m"),
    0xC6: ("ifnull", "j"), 0xC7: ("ifnonnull", "j"),
    0xC8: ("goto_w", "J"), 0xC9: ("jsr_w", "J"),
}

NEWARRAY_TYPES = {4: "Z", 5: "C", 6: "F", 7: "D", 8: "B", 9: "S", 10: "I", 11: "J"}

BRANCH_OPS = {name for name, fmt in OPCODES.values() if fmt in ("j", "J")}
RETURN_OPS = {"ireturn", "lreturn", "freturn", "dreturn", "areturn", "return"}
TERMINATORS = RETURN_OPS | {"athrow", "goto", "goto_w", "tableswitch", "lookupswitch", "ret"}


@dataclass(slots=True)
class Insn:
    pc: int
    op: str
    args: tuple[Any, ...] = ()
    # For switches: (default_target, {key: target})
    # For branches: absolute target pc in args[0]

    def __repr__(self) -> str:
        a = ", ".join(map(repr, self.args))
        return f"{self.pc:5d}: {self.op} {a}".rstrip()


def decode(code: bytes, cp: ConstantPool | None = None) -> Iterator[Insn]:
    """Decode bytecode. If `cp` is given, cp-index operands are resolved."""
    i, n = 0, len(code)
    s2 = struct.Struct(">h")
    s4 = struct.Struct(">i")
    while i < n:
        pc = i
        opc = code[i]
        i += 1
        try:
            op, fmt = OPCODES[opc]
        except KeyError:
            raise ValueError(f"unknown opcode 0x{opc:02x} at pc {pc}") from None
        args: tuple[Any, ...] = ()
        if fmt == "":
            pass
        elif fmt == "b":
            args = (code[i] - 256 if code[i] > 127 else code[i],)
            i += 1
        elif fmt == "B":
            args = (code[i],)
            i += 1
        elif fmt == "a":
            args = (NEWARRAY_TYPES[code[i]],)
            i += 1
        elif fmt == "s":
            args = (s2.unpack_from(code, i)[0],)
            i += 2
        elif fmt in ("c", "C"):
            if fmt == "C":
                idx = code[i]
                i += 1
            else:
                idx = (code[i] << 8) | code[i + 1]
                i += 2
            args = (_resolve(cp, op, idx),)
        elif fmt == "j":
            args = (pc + s2.unpack_from(code, i)[0],)
            i += 2
        elif fmt == "J":
            args = (pc + s4.unpack_from(code, i)[0],)
            i += 4
        elif fmt == "i":
            args = (code[i], code[i + 1] - 256 if code[i + 1] > 127 else code[i + 1])
            i += 2
        elif fmt == "m":
            idx = (code[i] << 8) | code[i + 1]
            args = (cp.class_name(idx) if cp else idx, code[i + 2])
            i += 3
        elif fmt == "I":
            idx = (code[i] << 8) | code[i + 1]
            args = (cp.member_ref(idx) if cp else idx,)
            i += 4
        elif fmt == "D":
            idx = (code[i] << 8) | code[i + 1]
            if cp:
                e = cp[idx]
                name, desc = cp.name_and_type(e.b)
                args = (e.a, name, desc)  # (bootstrap index, name, descriptor)
            else:
                args = (idx,)
            i += 4
        elif fmt == "T":
            i = (i + 3) & ~3
            default, low, high = s4.unpack_from(code, i)[0], s4.unpack_from(code, i + 4)[0], s4.unpack_from(code, i + 8)[0]
            i += 12
            table = {}
            for k in range(low, high + 1):
                table[k] = pc + s4.unpack_from(code, i)[0]
                i += 4
            args = (pc + default, table)
        elif fmt == "L":
            i = (i + 3) & ~3
            default, npairs = s4.unpack_from(code, i)[0], s4.unpack_from(code, i + 4)[0]
            i += 8
            table = {}
            for _ in range(npairs):
                table[s4.unpack_from(code, i)[0]] = pc + s4.unpack_from(code, i + 4)[0]
                i += 8
            args = (pc + default, table)
        elif fmt == "W":
            wop, _ = OPCODES[code[i]]
            idx = (code[i + 1] << 8) | code[i + 2]
            if wop == "iinc":
                args = (idx, s2.unpack_from(code, i + 3)[0])
                i += 5
            else:
                args = (idx,)
                i += 3
            op = wop
        yield Insn(pc, op, args)


def _resolve(cp: ConstantPool | None, op: str, idx: int) -> Any:
    if cp is None:
        return idx
    if op in ("ldc", "ldc_w", "ldc2_w"):
        return cp.loadable(idx)
    if op in ("new", "anewarray", "checkcast", "instanceof"):
        return cp.class_name(idx)
    return cp.member_ref(idx)
