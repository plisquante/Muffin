"""
Mojo 1.2 Code Generator (Optimized, Zero-GC, Structured Control Flow).

Emits clean, idiomatic Mojo 1.2 source code from a closed-world `Hierarchy`
and `MethodIR`s with:
- Structured `if` / `else` / `while` / `break` / `continue` control flow
  (no `while True: if _pc == ...` state machine on structured methods)
- Intra-method Escape Analysis lowering non-escaping `new`/`newarray` to
  zero-malloc, zero-RC `FrameArena` bump allocations
- Dead-temp pruning (zero unused-variable warnings, register-friendly locals)
- Closed-world devirtualization + O(1) class & interface `instanceof`/`checkcast`
- JVMS-exact arithmetic (`idiv_j`, `irem_j`, `f2i_j`, `d2l_j`, `fcmp_j`, `dcmp_j`)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .classfile import Member
from .hierarchy import ClassInfo, Hierarchy
from .ir import (
    KIND_D,
    KIND_F,
    KIND_I,
    KIND_J,
    KIND_R,
    LiftedBlock,
    MethodIR,
    Stmt,
    Val,
    jtype_to_kind,
    kind_default,
    kind_to_mojo,
    lift_method,
)
from .optimize import OptInfo, compute_non_escaping_params, optimize_method
from .structurer import (
    BlockNode,
    BreakNode,
    ContinueNode,
    DispatchNode,
    IfNode,
    SeqNode,
    WhileNode,
    structure_method,
)
from .types import JType, mangle_class, mangle_method, safe_ident

BUILTIN_CALLS = {
    ("java/lang/Object", "<init>", "()V"): "noop",
    ("java/lang/Object", "hashCode", "()I"): "obj_hash",
    ("java/lang/Object", "equals", "(Ljava/lang/Object;)Z"): "obj_eq",
    ("java/io/PrintStream", "println", "()V"): "println_empty",
    ("java/io/PrintStream", "println", "(I)V"): "println_val",
    ("java/io/PrintStream", "println", "(J)V"): "println_val",
    ("java/io/PrintStream", "println", "(F)V"): "println_val",
    ("java/io/PrintStream", "println", "(D)V"): "println_val",
    ("java/io/PrintStream", "println", "(Z)V"): "println_bool",
    ("java/io/PrintStream", "println", "(C)V"): "println_char",
    ("java/io/PrintStream", "println", "(Ljava/lang/String;)V"): "println_str",
    ("java/io/PrintStream", "println", "(Ljava/lang/Object;)V"): "println_str",
    ("java/lang/StringBuilder", "<init>", "()V"): "sb_init",
    ("java/lang/StringBuilder", "<init>", "(Ljava/lang/String;)V"): "sb_init_str",
    ("java/lang/StringBuilder", "<init>", "(I)V"): "sb_init",
    ("java/lang/StringBuilder", "append", "(Ljava/lang/String;)Ljava/lang/StringBuilder;"): "sb_app_str",
    ("java/lang/StringBuilder", "append", "(Ljava/lang/Object;)Ljava/lang/StringBuilder;"): "sb_app_str",
    ("java/lang/StringBuilder", "append", "(I)Ljava/lang/StringBuilder;"): "sb_app_val",
    ("java/lang/StringBuilder", "append", "(J)Ljava/lang/StringBuilder;"): "sb_app_val",
    ("java/lang/StringBuilder", "append", "(F)Ljava/lang/StringBuilder;"): "sb_app_val",
    ("java/lang/StringBuilder", "append", "(D)Ljava/lang/StringBuilder;"): "sb_app_val",
    ("java/lang/StringBuilder", "append", "(Z)Ljava/lang/StringBuilder;"): "sb_app_bool",
    ("java/lang/StringBuilder", "append", "(C)Ljava/lang/StringBuilder;"): "sb_app_char",
    ("java/lang/StringBuilder", "toString", "()Ljava/lang/String;"): "sb_to_str",
    ("java/lang/String", "equals", "(Ljava/lang/Object;)Z"): "str_eq",
    ("java/lang/String", "hashCode", "()I"): "str_hash",
    ("java/lang/String", "length", "()I"): "str_len",
    ("java/lang/String", "charAt", "(I)C"): "str_char_at",
    ("java/lang/String", "concat", "(Ljava/lang/String;)Ljava/lang/String;"): "str_concat",
    ("java/lang/String", "substring", "(I)Ljava/lang/String;"): "str_sub1",
    ("java/lang/String", "substring", "(II)Ljava/lang/String;"): "str_sub2",
    ("java/lang/String", "valueOf", "(I)Ljava/lang/String;"): "int_to_str",
    ("java/lang/String", "valueOf", "(J)Ljava/lang/String;"): "long_to_str",
    ("java/lang/Integer", "toString", "(I)Ljava/lang/String;"): "int_to_str",
    ("java/lang/Integer", "parseInt", "(Ljava/lang/String;)I"): "int_parse",
    ("java/lang/Integer", "bitCount", "(I)I"): "int_bitcount",
    ("java/lang/Integer", "numberOfLeadingZeros", "(I)I"): "int_clz",
    ("java/lang/Integer", "numberOfTrailingZeros", "(I)I"): "int_ctz",
    ("java/lang/Integer", "hashCode", "(I)I"): "id_arg0",
    ("java/lang/Long", "toString", "(J)Ljava/lang/String;"): "long_to_str",
    ("java/lang/Long", "bitCount", "(J)I"): "long_bitcount",
    ("java/lang/Long", "numberOfLeadingZeros", "(J)I"): "long_clz",
    ("java/lang/Long", "hashCode", "(J)I"): "long_hash",
    ("java/lang/Math", "abs", "(I)I"): "abs_i",
    ("java/lang/Math", "abs", "(J)J"): "abs_l",
    ("java/lang/Math", "abs", "(F)F"): "abs_f",
    ("java/lang/Math", "abs", "(D)D"): "abs_d",
    ("java/lang/Math", "min", "(II)I"): "min_i",
    ("java/lang/Math", "max", "(II)I"): "max_i",
    ("java/lang/Math", "min", "(FF)F"): "min_f",
    ("java/lang/Math", "max", "(FF)F"): "max_f",
    ("java/lang/Math", "min", "(DD)D"): "min_d",
    ("java/lang/Math", "max", "(DD)D"): "max_d",
    ("java/lang/Math", "sin", "(D)D"): "math_sin",
    ("java/lang/Math", "cos", "(D)D"): "math_cos",
    ("java/lang/Math", "tan", "(D)D"): "math_tan",
    ("java/lang/Math", "asin", "(D)D"): "math_asin",
    ("java/lang/Math", "acos", "(D)D"): "math_acos",
    ("java/lang/Math", "atan", "(D)D"): "math_atan",
    ("java/lang/Math", "sqrt", "(D)D"): "math_sqrt",
    ("java/lang/Math", "cbrt", "(D)D"): "math_cbrt",
    ("java/lang/Math", "exp", "(D)D"): "math_exp",
    ("java/lang/Math", "log", "(D)D"): "math_log",
    ("java/lang/Math", "log10", "(D)D"): "math_log10",
    ("java/lang/Math", "pow", "(DD)D"): "math_pow",
    ("java/lang/Math", "hypot", "(DD)D"): "math_hypot",
    ("java/lang/Math", "floor", "(D)D"): "math_floor",
    ("java/lang/Math", "ceil", "(D)D"): "math_ceil",
    ("java/lang/Math", "round", "(D)J"): "math_round_d",
    ("java/lang/Math", "round", "(F)I"): "math_round_f",
    ("java/lang/Math", "signum", "(D)D"): "math_signum_d",
    ("java/lang/Math", "signum", "(F)F"): "math_signum_f",
    ("java/lang/Math", "atan2", "(DD)D"): "math_atan2",
    ("java/lang/StrictMath", "sin", "(D)D"): "math_sin",
    ("java/lang/StrictMath", "cos", "(D)D"): "math_cos",
    ("java/lang/StrictMath", "sqrt", "(D)D"): "math_sqrt",
    ("java/lang/StrictMath", "floor", "(D)D"): "math_floor",
    ("java/lang/StrictMath", "ceil", "(D)D"): "math_ceil",
    ("java/lang/StrictMath", "atan2", "(DD)D"): "math_atan2",
    ("java/lang/StrictMath", "pow", "(DD)D"): "math_pow",
    ("java/lang/StrictMath", "exp", "(D)D"): "math_exp",
    ("java/lang/StrictMath", "log", "(D)D"): "math_log",
    ("java/lang/Float", "floatToRawIntBits", "(F)I"): "float_to_bits",
    ("java/lang/Float", "floatToIntBits", "(F)I"): "float_to_bits",
    ("java/lang/Float", "intBitsToFloat", "(I)F"): "bits_to_float",
    ("java/lang/Float", "isNaN", "(F)Z"): "float_isnan",
    ("java/lang/Float", "isInfinite", "(F)Z"): "float_isinf",
    ("java/lang/Double", "doubleToRawLongBits", "(D)J"): "double_to_bits",
    ("java/lang/Double", "doubleToLongBits", "(D)J"): "double_to_bits",
    ("java/lang/Double", "longBitsToDouble", "(J)D"): "bits_to_double",
    ("java/lang/Double", "isNaN", "(D)Z"): "double_isnan",
    ("java/lang/Double", "isInfinite", "(D)Z"): "double_isinf",
    ("java/util/Objects", "equals", "(Ljava/lang/Object;Ljava/lang/Object;)Z"): "objects_eq",
    ("java/util/Objects", "hashCode", "(Ljava/lang/Object;)I"): "objects_hash",
    ("java/util/Objects", "requireNonNull", "(Ljava/lang/Object;)Ljava/lang/Object;"): "id_arg0_ref",
    ("java/lang/System", "nanoTime", "()J"): "sys_nano",
    ("java/lang/System", "currentTimeMillis", "()J"): "sys_millis",
    ("java/lang/System", "identityHashCode", "(Ljava/lang/Object;)I"): "objects_hash",
    ("java/lang/System", "arraycopy", "(Ljava/lang/Object;ILjava/lang/Object;II)V"): "sys_arraycopy",
    ("java/lang/Runtime", "availableProcessors", "()I"): "rt_cpus",
    ("java/lang/Thread", "sleep", "(J)V"): "thread_sleep",
    ("java/lang/Thread", "onSpinWait", "()V"): "noop",
    ("java/util/Arrays", "fill", "([II)V"): "arrays_fill_i",
    ("jdk/internal/misc/Unsafe", "allocateMemory", "(J)J"): "unsafe_alloc",
    ("jdk/internal/misc/Unsafe", "reallocateMemory", "(JJ)J"): "unsafe_realloc",
    ("jdk/internal/misc/Unsafe", "freeMemory", "(J)V"): "unsafe_free",
    ("jdk/internal/misc/Unsafe", "setMemory", "(JJB)V"): "unsafe_memset",
    ("jdk/internal/misc/Unsafe", "copyMemory", "(JJJ)V"): "unsafe_memcpy",
    ("jdk/internal/misc/Unsafe", "getByte", "(J)B"): "unsafe_get_i8",
    ("jdk/internal/misc/Unsafe", "putByte", "(JB)V"): "unsafe_put_i8",
    ("jdk/internal/misc/Unsafe", "getShort", "(J)S"): "unsafe_get_i16",
    ("jdk/internal/misc/Unsafe", "putShort", "(JS)V"): "unsafe_put_i16",
    ("jdk/internal/misc/Unsafe", "getInt", "(J)I"): "unsafe_get_i32",
    ("jdk/internal/misc/Unsafe", "putInt", "(JI)V"): "unsafe_put_i32",
    ("jdk/internal/misc/Unsafe", "getLong", "(J)J"): "unsafe_get_i64",
    ("jdk/internal/misc/Unsafe", "putLong", "(JJ)V"): "unsafe_put_i64",
    ("jdk/internal/misc/Unsafe", "getFloat", "(J)F"): "unsafe_get_f32",
    ("jdk/internal/misc/Unsafe", "putFloat", "(JF)V"): "unsafe_put_f32",
    ("jdk/internal/misc/Unsafe", "getDouble", "(J)D"): "unsafe_get_f64",
    ("jdk/internal/misc/Unsafe", "putDouble", "(JD)V"): "unsafe_put_f64",
    ("sun/misc/Unsafe", "allocateMemory", "(J)J"): "unsafe_alloc",
    ("sun/misc/Unsafe", "freeMemory", "(J)V"): "unsafe_free",
    ("sun/misc/Unsafe", "getInt", "(J)I"): "unsafe_get_i32",
    ("sun/misc/Unsafe", "putInt", "(JI)V"): "unsafe_put_i32",
    ("sun/misc/Unsafe", "getLong", "(J)J"): "unsafe_get_i64",
    ("sun/misc/Unsafe", "putLong", "(JJ)V"): "unsafe_put_i64",
}


@dataclass
class CodegenStats:
    classes_emitted: int = 0
    methods_emitted: int = 0
    structured_methods: int = 0
    dispatch_methods: int = 0
    arena_allocs_lowered: int = 0
    calls_devirtualized: int = 0
    calls_virtual: int = 0
    current_shard: int | None = None
    class_to_shard: dict[str, int] | None = None
    vdisp_to_shard: dict[tuple[str, str, str], int] | None = None
    vdisp_needed: set[tuple[str, str, str]] = field(default_factory=set)
    large_ifaces: set[str] = field(default_factory=set)


def _resolve_static_owner(hierarchy: Hierarchy, owner: str, fname: str) -> str | None:
    cur: str | None = owner
    seen: set[str] = set()
    while cur and cur in hierarchy.classes and cur not in seen:
        seen.add(cur)
        ci = hierarchy.classes[cur]
        if any(fl.name == fname for fl in ci.static_fields):
            return cur
        for iface in ci.cf.interfaces:
            res = _resolve_static_owner(hierarchy, iface, fname)
            if res is not None:
                return res
        cur = ci.cf.super_name
    return owner if owner in hierarchy.classes else None


def _escape_mojo_str(s: str) -> str:
    out: list[str] = []
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 32 or (0xD800 <= o <= 0xDFFF):
            out.append(f"\\u{o:04x}")
        else:
            out.append(ch)
    return "".join(out)


def _emit_runtime_imports(w) -> None:
    w("from std.ffi import external_call")
    w("from std.memory import MutOpaquePointer, OptionalPointer, Pointer")
    w("from std.origin import MutUntrackedOrigin")
    w("from std.sys import size_of")
    w("from runtime.muffin import (")
    for sym in (
        "ArenaMark", "CID_ARRAY_B1", "CID_ARRAY_B2", "CID_ARRAY_B4", "CID_ARRAY_B8",
        "CID_ARRAY_REF", "FLAG_ARENA", "FrameArena", "JArrayData", "JRef", "JThrowable",
        "ObjHeader", "RawFn", "alloc_arena_obj", "alloc_raw", "array_get", "array_len",
        "array_set", "arraycopy", "arraycopy_any", "check_cast", "d2i_j", "d2l_j",
        "dcmp_j", "f2i_j", "f2l_j", "fcmp_j", "free_raw", "idiv_j", "irem_j", "is_instance",
        "jarrays_fill_i", "jbits_to_double", "jbits_to_float", "jdouble_isinf",
        "jdouble_isnan", "jdouble_to_bits", "jfloat_isinf", "jfloat_isnan", "jfloat_to_bits",
        "jint_bit_count", "jint_clz", "jint_ctz", "jint_parse", "jint_to_string",
        "jlong_bit_count", "jlong_clz", "jlong_to_string", "jmath_abs_d", "jmath_abs_f",
        "jmath_abs_i", "jmath_abs_l", "jmath_acos", "jmath_asin", "jmath_atan", "jmath_atan2",
        "jmath_cbrt", "jmath_ceil", "jmath_cos", "jmath_exp", "jmath_floor", "jmath_hypot",
        "jmath_log", "jmath_log10", "jmath_max_d", "jmath_max_f", "jmath_max_i",
        "jmath_min_d", "jmath_min_f", "jmath_min_i", "jmath_pow", "jmath_round_d",
        "jmath_round_f", "jmath_signum_d", "jmath_signum_f", "jmath_sin", "jmath_sqrt",
        "jmath_tan", "jref_from_raw", "jruntime_cpus", "jsys_current_millis", "jsys_nano_time",
        "jthread_sleep", "junsafe_alloc", "junsafe_free", "junsafe_get_f32", "junsafe_get_f64",
        "junsafe_get_i16", "junsafe_get_i32", "junsafe_get_i64", "junsafe_get_i8",
        "junsafe_memcpy", "junsafe_memset", "junsafe_put_f32", "junsafe_put_f64",
        "junsafe_put_i16", "junsafe_put_i32", "junsafe_put_i64", "junsafe_put_i8",
        "junsafe_realloc", "ldiv_j", "lrem_j", "new_array", "new_array_arena",
        "new_multi_array", "new_sb", "new_string", "sb_append_bool", "sb_append_char",
        "sb_append_str", "sb_append_val", "sb_to_string", "string_char_at", "string_concat",
        "string_equals", "string_hash", "string_length", "string_substring", "string_value",
    ):
        w(f"    {sym},")
    w(")")
    w("")


def compress_cid_runs(cids: list[int]) -> list[tuple[int, int]]:
    """Compress sorted integer class IDs into contiguous inclusive `(lo, hi)` runs."""
    if not cids:
        return []
    runs: list[tuple[int, int]] = []
    lo = hi = cids[0]
    for c in cids[1:]:
        if c == hi + 1:
            hi = c
        elif c > hi:
            runs.append((lo, hi))
            lo = hi = c
    runs.append((lo, hi))
    return runs


def _iface_helper_name(iface: str) -> str:
    return f"_if_{mangle_class(iface)[2:]}"


def _emit_runs_binary_tree(w, ind: str, cid_v: str, runs: list[tuple[int, int]]) -> None:
    if len(runs) <= 4:
        parts = [
            f"{cid_v} == Int32({lo})" if lo == hi else f"({cid_v} >= Int32({lo}) and {cid_v} <= Int32({hi}))"
            for lo, hi in runs
        ]
        w(f"{ind}return {' or '.join(parts)}")
        return
    mid = len(runs) // 2
    mid_lo = runs[mid][0]
    w(f"{ind}if {cid_v} < Int32({mid_lo}):")
    _emit_runs_binary_tree(w, ind + "    ", cid_v, runs[:mid])
    w(f"{ind}else:")
    _emit_runs_binary_tree(w, ind + "    ", cid_v, runs[mid:])


def emit_shared_module(
    hierarchy: Hierarchy,
    stats: CodegenStats | None = None,
    *,
    num_shards: int = 0,
    large_ifaces: set[str] | None = None,
) -> tuple[str, list[str]]:
    """Emit `shared.mojo` (minimal runtime `JStatics` when `num_shards > 0`, full structs in single-file mode)."""
    out: list[str] = []
    w = out.append
    exported_syms = ["JStatics", "JStaticsPtr"]
    w('"""Muffin Shared Class Layouts & Static Context (Mojo 1.2)."""')
    w("")
    _emit_runtime_imports(w)
    w("comptime JStaticsPtr = Pointer[JStatics, MutUntrackedOrigin]")
    w("")
    w("struct JStatics:")
    w("    var arena: FrameArena")
    w("    var has_exc: Bool")
    w("    var exc_cid: Int32")
    w("    var exc_obj: JRef")
    w("    var ret_ref: JRef")
    w("    var shard_statics: OptionalPointer[OptionalPointer[NoneType, MutUntrackedOrigin], MutUntrackedOrigin]")
    w("    var shard_count: Int32")

    static_fields: list[tuple[str, str, str]] = []
    if num_shards == 0:
        for cname in sorted(hierarchy.classes):
            ci = hierarchy.classes[cname]
            for fl in ci.static_fields:
                sname = f"sf_{mangle_class(cname)[2:]}_{safe_ident(fl.name)}"
                mtype = kind_to_mojo(jtype_to_kind(JType(fl.descriptor)))
                mdef = kind_default(jtype_to_kind(JType(fl.descriptor)))
                static_fields.append((sname, mtype, mdef))
        for sname, mtype, _ in static_fields:
            w(f"    var {sname}: {mtype}")

    w("    def __init__(out self, nshards: Int32 = 0):")
    w("        self.arena = FrameArena()")
    w("        self.has_exc = False")
    w("        self.exc_cid = 0")
    w("        self.exc_obj = JRef()")
    w("        self.ret_ref = JRef()")
    w("        self.shard_count = nshards")
    w("        if nshards > 0:")
    w('            self.shard_statics = external_call["calloc", OptionalPointer[OptionalPointer[NoneType, MutUntrackedOrigin], MutUntrackedOrigin]](Int(nshards), 8)')
    w("        else:")
    w("            self.shard_statics = {}")
    for sname, _, mdef in static_fields:
        w(f"        self.{sname} = {mdef}")
    w("    def __deinit__(deinit self):")
    w("        if self.shard_statics:")
    w("            var arr = self.shard_statics.unsafe_value()")
    w("            for i in range(Int(self.shard_count)):")
    w("                if arr.unsafe_offset(i)[]:")
    w('                    external_call["free", NoneType](arr.unsafe_offset(i)[].unsafe_value())')
    w('            external_call["free", NoneType](arr.unsafe_bitcast[NoneType]())')
    w("")

    for iface in sorted(large_ifaces or ()):
        ids = hierarchy.interface_impl_ids.get(iface, [])
        runs = compress_cid_runs(ids)
        if not runs:
            continue
        hname = _iface_helper_name(iface)
        exported_syms.append(hname)
        w(f"def {hname}(cid: Int32) -> Bool:")
        _emit_runs_binary_tree(w, "    ", "cid", runs)
        w("")

    if num_shards > 0:
        return "\n".join(out), exported_syms

    for cname in sorted(hierarchy.classes):
        ci = hierarchy.classes[cname]
        if ci.cf.is_interface:
            continue
        if stats is not None:
            stats.classes_emitted += 1
        sname = mangle_class(cname) + "Data"
        drop_fn = f"drop_{mangle_class(cname)}"
        new_fn = f"new_{mangle_class(cname)}"
        new_a_fn = f"new_arena_{mangle_class(cname)}"
        exported_syms.extend((sname, drop_fn, new_fn, new_a_fn))
        w(f"struct {sname}:")
        w("    var hdr: ObjHeader")
        for fl in ci.all_fields:
            mtype = kind_to_mojo(jtype_to_kind(JType(fl.descriptor)))
            w(f"    var f_{safe_ident(fl.mojo_name)}: {mtype}")
        w("")
        w(f"def {drop_fn}(p: Pointer[ObjHeader, MutUntrackedOrigin]):")
        w(f"    var op = p.unsafe_bitcast[{sname}]()")
        w("    op.unsafe_deinit_pointee()")
        w("    free_raw(op)")
        w("")
        w(f"def {new_fn}() raises JThrowable -> JRef:")
        w(f"    var raw = alloc_raw[{sname}]()")
        w(f"    raw[].hdr = ObjHeader({ci.class_id}, {{}}, {drop_fn})")
        for fl in ci.all_fields:
            mdef = kind_default(jtype_to_kind(JType(fl.descriptor)))
            w(f"    raw[].f_{safe_ident(fl.mojo_name)} = {mdef}")
        w("    return JRef(raw.unsafe_bitcast[ObjHeader]())")
        w("")
        w("@always_inline")
        w(f"def {new_a_fn}(_s: JStaticsPtr) -> JRef:")
        w(f"    var raw = alloc_arena_obj[{sname}](_s[].arena)")
        w(f"    raw[].hdr = ObjHeader({ci.class_id}, FLAG_ARENA)")
        for fl in ci.all_fields:
            mdef = kind_default(jtype_to_kind(JType(fl.descriptor)))
            w(f"    raw[].f_{safe_ident(fl.mojo_name)} = {mdef}")
        w("    return JRef(raw.unsafe_bitcast[ObjHeader]())")
        w("")
    return "\n".join(out), exported_syms


def emit_shard_classes_and_statics(
    out: list[str],
    hierarchy: Hierarchy,
    shard_classes: list[str],
    shard_idx: int,
    stats: CodegenStats,
    *,
    cross_news: set[str],
    cross_gf: set[tuple[str, str]],
    cross_pf: set[tuple[str, str]],
    cross_gs: set[tuple[str, str]],
    cross_ps: set[tuple[str, str]],
) -> None:
    """Emit per-shard static globals, class structs, and only cross-referenced C-ABI trampolines."""
    w = out.append
    static_fields: list[tuple[str, str, str, str, str]] = []
    for cname in sorted(shard_classes):
        ci = hierarchy.classes[cname]
        for fl in ci.static_fields:
            sname = f"sf_{mangle_class(cname)[2:]}_{safe_ident(fl.name)}"
            k = jtype_to_kind(JType(fl.descriptor))
            static_fields.append((cname, fl.name, sname, kind_to_mojo(k), kind_default(k)))

    if static_fields:
        w("struct _ShardStatics:")
        for _, _, sname, mtype, _ in static_fields:
            w(f"    var {sname}: {mtype}")
        w("    def __init__(out self):")
        for _, _, sname, _, mdef in static_fields:
            w(f"        self.{sname} = {mdef}")
        w("")
        w("@always_inline")
        w("def _sf(_s: JStaticsPtr) raises JThrowable -> Pointer[_ShardStatics, MutUntrackedOrigin]:")
        w(f"    var slot = _s[].shard_statics.unsafe_value().unsafe_offset({shard_idx})")
        w("    if not slot[]:")
        w("        var sp = alloc_raw[_ShardStatics]()")
        w("        sp.unsafe_write(_ShardStatics())")
        w("        slot[] = sp.unsafe_bitcast[NoneType]()")
        w("    return slot[].unsafe_value().unsafe_bitcast[_ShardStatics]()")
        w("")

        for cname, fname, sname, mtype, _ in static_fields:
            k_is_ref = (mtype == "JRef")
            if (cname, fname) in cross_gs:
                sym = f"x_gs_{mangle_class(cname)[2:]}_{safe_ident(fname)}"
                cret = "NoneType" if k_is_ref else mtype
                w(f'@export("{sym}")')
                w(f'def _{sym}(_s: JStaticsPtr) abi("C") -> {cret}:')
                w("    try:")
                if k_is_ref:
                    w(f"        _s[].ret_ref = _sf(_s)[].{sname}.copy()")
                    w("        return")
                else:
                    w(f"        return _sf(_s)[].{sname}")
                w("    except _exc:")
                w("        _s[].has_exc = True")
                w("        _s[].exc_cid = _exc.class_id")
                w("        _s[].exc_obj = _exc.obj.copy()")
                w("        return" if k_is_ref else f"        return {kind_default(jtype_to_kind(JType('I' if mtype == 'Int32' else 'J' if mtype == 'Int64' else 'F' if mtype == 'Float32' else 'D')))}")
                w("")
            if (cname, fname) in cross_ps:
                sym = f"x_ps_{mangle_class(cname)[2:]}_{safe_ident(fname)}"
                parg = "raw_v: OptionalPointer[ObjHeader, MutUntrackedOrigin]" if k_is_ref else f"v: {mtype}"
                w(f'@export("{sym}")')
                w(f'def _{sym}(_s: JStaticsPtr, {parg}) abi("C") -> NoneType:')
                w("    try:")
                if k_is_ref:
                    w(f"        _sf(_s)[].{sname} = jref_from_raw(raw_v)")
                else:
                    w(f"        _sf(_s)[].{sname} = v")
                w("        return")
                w("    except _exc:")
                w("        _s[].has_exc = True")
                w("        _s[].exc_cid = _exc.class_id")
                w("        _s[].exc_obj = _exc.obj.copy()")
                w("        return")
                w("")

    for cname in sorted(shard_classes):
        ci = hierarchy.classes[cname]
        if ci.cf.is_interface:
            continue
        stats.classes_emitted += 1
        sname = mangle_class(cname) + "Data"
        mc = mangle_class(cname)
        drop_fn = f"drop_{mc}"
        new_fn = f"new_{mc}"
        new_a_fn = f"new_arena_{mc}"
        w(f"struct {sname}:")
        w("    var hdr: ObjHeader")
        for fl in ci.all_fields:
            mtype = kind_to_mojo(jtype_to_kind(JType(fl.descriptor)))
            w(f"    var f_{safe_ident(fl.mojo_name)}: {mtype}")
        w("")
        w(f"def {drop_fn}(p: Pointer[ObjHeader, MutUntrackedOrigin]):")
        w(f"    var op = p.unsafe_bitcast[{sname}]()")
        w("    op.unsafe_deinit_pointee()")
        w("    free_raw(op)")
        w("")
        w(f"def {new_fn}() raises JThrowable -> JRef:")
        w(f"    var raw = alloc_raw[{sname}]()")
        w(f"    raw[].hdr = ObjHeader({ci.class_id}, {{}}, {drop_fn})")
        for fl in ci.all_fields:
            mdef = kind_default(jtype_to_kind(JType(fl.descriptor)))
            w(f"    raw[].f_{safe_ident(fl.mojo_name)} = {mdef}")
        w("    return JRef(raw.unsafe_bitcast[ObjHeader]())")
        w("")
        w("@always_inline")
        w(f"def {new_a_fn}(_s: JStaticsPtr) -> JRef:")
        w(f"    var raw = alloc_arena_obj[{sname}](_s[].arena)")
        w(f"    raw[].hdr = ObjHeader({ci.class_id}, FLAG_ARENA)")
        for fl in ci.all_fields:
            mdef = kind_default(jtype_to_kind(JType(fl.descriptor)))
            w(f"    raw[].f_{safe_ident(fl.mojo_name)} = {mdef}")
        w("    return JRef(raw.unsafe_bitcast[ObjHeader]())")
        w("")

        if cname in cross_news:
            w(f'@export("x_{new_fn}")')
            w(f'def _x_{new_fn}(_s: JStaticsPtr) abi("C") -> NoneType:')
            w("    try:")
            w(f"        _s[].ret_ref = {new_fn}()")
            w("        return")
            w("    except _exc:")
            w("        _s[].has_exc = True")
            w("        _s[].exc_cid = _exc.class_id")
            w("        _s[].exc_obj = _exc.obj.copy()")
            w("        return")
            w("")
            w(f'@export("x_{new_a_fn}")')
            w(f'def _x_{new_a_fn}(_s: JStaticsPtr) abi("C") -> NoneType:')
            w(f"    _s[].ret_ref = {new_a_fn}(_s)")
            w("    return")
            w("")

        for fl in ci.all_fields:
            fk = jtype_to_kind(JType(fl.descriptor))
            mtype = kind_to_mojo(fk)
            fn_id = f"{mc[2:]}_{safe_ident(fl.name)}"
            mfield = safe_ident(fl.mojo_name)
            if (cname, fl.name) in cross_gf:
                sym = f"x_gf_{fn_id}"
                cret = "NoneType" if fk == KIND_R else mtype
                w(f'@export("{sym}")')
                w(f'def _{sym}(_s: JStaticsPtr, raw_o: OptionalPointer[ObjHeader, MutUntrackedOrigin]) abi("C") -> {cret}:')
                w("    try:")
                w(f"        var op = jref_from_raw(raw_o).non_null().unsafe_bitcast[{sname}]()")
                if fk == KIND_R:
                    w(f"        _s[].ret_ref = op[].f_{mfield}.copy()")
                    w("        return")
                else:
                    w(f"        return op[].f_{mfield}")
                w("    except _exc:")
                w("        _s[].has_exc = True")
                w("        _s[].exc_cid = _exc.class_id")
                w("        _s[].exc_obj = _exc.obj.copy()")
                w("        return" if fk == KIND_R else f"        return {kind_default(fk)}")
                w("")
            if (cname, fl.name) in cross_pf:
                sym = f"x_pf_{fn_id}"
                parg = "raw_v: OptionalPointer[ObjHeader, MutUntrackedOrigin]" if fk == KIND_R else f"v: {mtype}"
                w(f'@export("{sym}")')
                w(f'def _{sym}(_s: JStaticsPtr, raw_o: OptionalPointer[ObjHeader, MutUntrackedOrigin], {parg}) abi("C") -> NoneType:')
                w("    try:")
                w(f"        var op = jref_from_raw(raw_o).non_null().unsafe_bitcast[{sname}]()")
                if fk == KIND_R:
                    w(f"        op[].f_{mfield} = jref_from_raw(raw_v)")
                else:
                    w(f"        op[].f_{mfield} = v")
                w("        return")
                w("    except _exc:")
                w("        _s[].has_exc = True")
                w("        _s[].exc_cid = _exc.class_id")
                w("        _s[].exc_obj = _exc.obj.copy()")
                w("        return")
                w("")


def emit_mojo_program(
    hierarchy: Hierarchy,
    *,
    entry_class: str | None = None,
) -> tuple[str, CodegenStats]:
    stats = CodegenStats()
    out: list[str] = []
    w = out.append

    # 1. Pre-lift all methods so EA & large-interface scanning run before emission
    lifted_list: list[tuple[ClassInfo, Member, MethodIR]] = []
    for cname in sorted(hierarchy.classes):
        ci = hierarchy.classes[cname]
        for m in ci.cf.methods:
            if m.code is None:
                continue
            lifted_list.append((ci, m, lift_method(ci.cf, m)))

    for _, _, ir in lifted_list:
        for blk in ir.blocks.values():
            for st in blk.stmts:
                if st.op in ("instanceof", "checkcast"):
                    tc = st.args[0]
                    tci = hierarchy.classes.get(tc)
                    if tci is not None and tci.cf.is_interface and tc not in stats.large_ifaces:
                        ids = hierarchy.interface_impl_ids.get(tc, [])
                        if len(compress_cid_runs(ids)) > 16:
                            stats.large_ifaces.add(tc)

    shared_src, _ = emit_shared_module(hierarchy, stats, large_ifaces=stats.large_ifaces)
    out.append(shared_src)

    non_escaping_params = compute_non_escaping_params([ir for _, _, ir in lifted_list])

    for ci, m, ir in lifted_list:
        opt = optimize_method(ir, non_escaping_params=non_escaping_params)
        ast = structure_method(ir)
        stats.methods_emitted += 1
        if isinstance(ast, DispatchNode):
            stats.dispatch_methods += 1
        else:
            stats.structured_methods += 1
        stats.arena_allocs_lowered += len(opt.arena_allocs)
        _emit_method(out, hierarchy, ci, m, ir, opt, ast, stats)
        w("")

    for v_owner, v_mname, v_mdesc in sorted(stats.vdisp_needed):
        emit_vdisp_function(out, hierarchy, v_owner, v_mname, v_mdesc, stats, export_c_abi=False)

    # 4. Optional main() entrypoint
    if entry_class and entry_class in hierarchy.classes:
        ci = hierarchy.classes[entry_class]
        has_main = any(
            m.is_static() and m.name == "main" and m.descriptor == "([Ljava/lang/String;)V"
            for m in ci.cf.methods
        )
        if has_main:
            main_fn = mangle_method(entry_class, "main", "([Ljava/lang/String;)V")
            w("def main() raises:")
            w("    var statics = alloc_raw[JStatics]()")
            w("    statics.unsafe_write(JStatics())")
            for cname in sorted(hierarchy.classes):
                cci = hierarchy.classes[cname]
                if any(mm.name == "<clinit>" and mm.code is not None for mm in cci.cf.methods):
                    w(f"    {mangle_method(cname, '<clinit>', '()V')}(statics)")
            w("    var argv = new_array[JRef](9, 0)")
            w(f"    {main_fn}(statics, argv)")
            w("    statics.unsafe_deinit_pointee()")
            w("    free_raw(statics)")
            w("")

    return "\n".join(out), stats


def _emit_method(
    out: list[str],
    hierarchy: Hierarchy,
    ci: ClassInfo,
    m: Member,
    ir: MethodIR,
    opt: OptInfo,
    ast: object,
    stats: CodegenStats,
) -> None:
    w = out.append
    fn_name = mangle_method(ir.owner, ir.name, ir.descriptor)

    sig_params = ["_s: JStaticsPtr"]
    param_bind: list[tuple[int, str]] = []
    loc_idx = 0
    if not ir.is_static:
        sig_params.append("arg0: JRef")
        param_bind.append((0, KIND_R))
        loc_idx = 1
    for i, pt in enumerate(ir.params):
        k = jtype_to_kind(pt)
        sig_params.append(f"p{i}: {kind_to_mojo(k)}")
        param_bind.append((loc_idx, k))
        loc_idx += 2 if pt.is_wide else 1

    op_count = sum(len(blk.stmts) for blk in ir.blocks.values())
    is_recursive = any(
        st.op == "call" and st.args[1:4] == (ir.owner, ir.name, ir.descriptor)
        for blk in ir.blocks.values()
        for st in blk.stmts
    )
    if (
        not opt.has_loop
        and op_count <= 45
        and not is_recursive
        and ir.name not in ("main", "<clinit>")
        and stats.current_shard is None
    ):
        w("@always_inline")
    ret_mojo = "None" if ir.ret_type.is_void else kind_to_mojo(jtype_to_kind(ir.ret_type))
    w(f"def {fn_name}({', '.join(sig_params)}) raises JThrowable -> {ret_mojo}:")

    from .patches import BUILTIN_PATCHES

    patch = BUILTIN_PATCHES.get((ir.owner, ir.name, ir.descriptor))
    if patch is not None:
        w(f"    return {patch.mojo_expr}")
        return

    # Check which parameter locals are read-only (never overwritten by store or iinc)
    written_locals: set[str] = set()
    for blk in ir.blocks.values():
        for st in blk.stmts:
            if st.dst is not None:
                written_locals.add(st.dst.name)
            elif st.op == "iinc":
                written_locals.add(st.args[0].name)

    param_aliases: dict[str, tuple[str, str]] = {}
    p_cursor = 0
    if not ir.is_static:
        lv = ir.locals_used[(0, KIND_R)]
        if lv.name not in written_locals:
            param_aliases[lv.name] = ("arg0", KIND_R)
        p_cursor = 1
    for i, (lidx, k) in enumerate(param_bind[p_cursor:]):
        lv = ir.locals_used[(lidx, k)]
        if lv.name not in written_locals:
            param_aliases[lv.name] = (f"p{i}", k)

    # Declare only live non-aliased variables
    declared: set[str] = set()
    for val in (
        *sorted(ir.locals_used.values(), key=lambda v: v.name),
        *sorted(ir.stack_slots.values(), key=lambda v: v.name),
        *ir.temps,
    ):
        if val.name in opt.live_vars and val.name not in param_aliases and val.name not in declared:
            declared.add(val.name)
            w(f"    var {val.name}: {kind_to_mojo(val.kind)} = {kind_default(val.kind)}")

    # Copy incoming params into live non-aliased locals
    p_cursor = 0
    if not ir.is_static:
        lv = ir.locals_used[(0, KIND_R)]
        if lv.name in declared:
            w(f"    {lv.name} = arg0.copy()")
        p_cursor = 1
    for i, (lidx, k) in enumerate(param_bind[p_cursor:]):
        lv = ir.locals_used[(lidx, k)]
        if lv.name in declared:
            pname = f"p{i}"
            w(f"    {lv.name} = {pname + '.copy()' if k == KIND_R else pname}")

    if param_aliases:
        _apply_param_aliases(ir, param_aliases)

    use_arena = bool(opt.arena_allocs)
    if use_arena:
        w("    var _amark = _s[].arena.mark()")
        w("    try:")
        base_ind = "        "
    else:
        base_ind = "    "

    if isinstance(ast, DispatchNode):
        _emit_dispatch(out, base_ind, hierarchy, ci, ast.blocks, opt, stats)
    else:
        _emit_ast(out, base_ind, hierarchy, ci, ast, opt, stats)
        # Ensure void structured methods always have a clean fallback return
        if ir.ret_type.is_void and not isinstance(ast, BlockNode):
            w(f"{base_ind}return")

    if use_arena:
        w("    finally:")
        w("        _s[].arena.restore(_amark)")


def _ast_has_arena_alloc(node: object, arena_allocs: set[str]) -> bool:
    if not arena_allocs:
        return False
    if isinstance(node, BlockNode):
        return any(st.dst is not None and st.dst.name in arena_allocs for st in node.block.stmts)
    if isinstance(node, SeqNode):
        return any(_ast_has_arena_alloc(item, arena_allocs) for item in node.items)
    if isinstance(node, WhileNode):
        return _ast_has_arena_alloc(node.body, arena_allocs)
    if isinstance(node, IfNode):
        return _ast_has_arena_alloc(node.then_node, arena_allocs) or _ast_has_arena_alloc(node.else_node, arena_allocs)
    return False


def _emit_ast(
    out: list[str],
    ind: str,
    hierarchy: Hierarchy,
    ci: ClassInfo,
    node: object,
    opt: OptInfo,
    stats: CodegenStats,
) -> None:
    w = out.append
    if isinstance(node, SeqNode):
        for item in node.items:
            _emit_ast(out, ind, hierarchy, ci, item, opt, stats)
    elif isinstance(node, BlockNode):
        _emit_block_stmts(out, ind, hierarchy, ci, node.block, opt, stats, emit_term=node.emit_term)
    elif isinstance(node, ContinueNode):
        w(f"{ind}continue")
    elif isinstance(node, BreakNode):
        w(f"{ind}break")
    elif isinstance(node, WhileNode):
        w(f"{ind}while True:")
        if opt.has_loop and _ast_has_arena_alloc(node.body, opt.arena_allocs):
            w(f"{ind}    var _lmark = _s[].arena.mark()")
            w(f"{ind}    try:")
            _emit_ast(out, ind + "        ", hierarchy, ci, node.body, opt, stats)
            w(f"{ind}    finally:")
            w(f"{ind}        _s[].arena.restore(_lmark)")
        else:
            _emit_ast(out, ind + "    ", hierarchy, ci, node.body, opt, stats)
    elif isinstance(node, IfNode):
        cond = _cond_expr(node.cond_stmt)
        w(f"{ind}if {cond}:")
        then_empty = isinstance(node.then_node, SeqNode) and not node.then_node.items
        if then_empty:
            w(f"{ind}    pass")
        else:
            _emit_ast(out, ind + "    ", hierarchy, ci, node.then_node, opt, stats)
        else_empty = isinstance(node.else_node, SeqNode) and not node.else_node.items
        if not else_empty:
            w(f"{ind}else:")
            _emit_ast(out, ind + "    ", hierarchy, ci, node.else_node, opt, stats)


def _emit_dispatch(
    out: list[str],
    ind: str,
    hierarchy: Hierarchy,
    ci: ClassInfo,
    blocks: list[LiftedBlock],
    opt: OptInfo,
    stats: CodegenStats,
) -> None:
    w = out.append
    w(f"{ind}var _pc: Int32 = 0")
    w(f"{ind}while True:")
    first = True
    for blk in blocks:
        kw = "if" if first else "elif"
        first = False
        w(f"{ind}    {kw} _pc == {blk.pc}:")
        bind = ind + "        "
        if blk.handlers:
            w(f"{bind}try:")
            _emit_block_stmts(out, bind + "    ", hierarchy, ci, blk, opt, stats, emit_term=True)
            w(f"{bind}except _exc:")
            for idx_h, (ctype, hpc) in enumerate(blk.handlers):
                s0_assign = "s0_R = _exc.obj.copy()" if "s0_R" in opt.live_vars else ""
                if ctype is None or len(blk.handlers) == 1:
                    if s0_assign:
                        w(f"{bind}    {s0_assign}")
                    w(f"{bind}    _pc = {hpc}")
                    w(f"{bind}    continue")
                    break
                h_kw = "if" if idx_h == 0 else "elif"
                if ctype in hierarchy.classes:
                    tci = hierarchy.classes[ctype]
                    w(f"{bind}    {h_kw} _exc.class_id >= {tci.class_id} and _exc.class_id <= {tci.max_sub_id}:")
                else:
                    w(f"{bind}    {h_kw} True:")
                if s0_assign:
                    w(f"{bind}        {s0_assign}")
                w(f"{bind}        _pc = {hpc}")
                w(f"{bind}        continue")
        else:
            _emit_block_stmts(out, bind, hierarchy, ci, blk, opt, stats, emit_term=True)


def _copy_if_ref(val: Val) -> str:
    return f"{val.name}.copy()" if val.kind == KIND_R else val.name


def _cond_expr(st: Stmt) -> str:
    if st.op == "if1":
        iop, v, _, _ = st.args
        return {
            "ifeq": f"{v.name} == 0",
            "ifne": f"{v.name} != 0",
            "iflt": f"{v.name} < 0",
            "ifge": f"{v.name} >= 0",
            "ifgt": f"{v.name} > 0",
            "ifle": f"{v.name} <= 0",
            "ifnull": f"{v.name}.is_null()",
            "ifnonnull": f"not {v.name}.is_null()",
        }[iop]
    iop, lhs, rhs, _, _ = st.args
    return {
        "if_icmpeq": f"{lhs.name} == {rhs.name}",
        "if_icmpne": f"{lhs.name} != {rhs.name}",
        "if_icmplt": f"{lhs.name} < {rhs.name}",
        "if_icmpge": f"{lhs.name} >= {rhs.name}",
        "if_icmpgt": f"{lhs.name} > {rhs.name}",
        "if_icmple": f"{lhs.name} <= {rhs.name}",
        "if_acmpeq": f"{lhs.name}.same({rhs.name})",
        "if_acmpne": f"not {lhs.name}.same({rhs.name})",
    }[iop]


def _emit_block_stmts(
    out: list[str],
    ind: str,
    hierarchy: Hierarchy,
    ci: ClassInfo,
    blk: LiftedBlock,
    opt: OptInfo,
    stats: CodegenStats,
    *,
    emit_term: bool,
) -> None:
    w = out.append
    for st in blk.stmts:
        op, dst, args = st.op, st.dst, st.args
        if op == "const":
            val = args[0]
            if val is None:
                w(f"{ind}{dst.name} = JRef()")
            elif dst.kind == KIND_I:
                w(f"{ind}{dst.name} = Int32({val})")
            elif dst.kind == KIND_J:
                w(f"{ind}{dst.name} = Int64({val})")
            elif dst.kind in (KIND_F, KIND_D):
                import math as _m

                ft = "Float32" if dst.kind == KIND_F else "Float64"
                fv = float(val)
                if _m.isnan(fv):
                    w(f"{ind}{dst.name} = {ft}(0.0) / {ft}(0.0)")
                elif _m.isinf(fv):
                    num = "1.0" if fv > 0 else "-1.0"
                    w(f"{ind}{dst.name} = {ft}({num}) / {ft}(0.0)")
                else:
                    w(f"{ind}{dst.name} = {ft}({val})")
        elif op == "const_str":
            w(f'{ind}{dst.name} = new_string("{_escape_mojo_str(args[0])}")')
        elif op == "copy":
            if dst.name in opt.live_vars:
                w(f"{ind}{dst.name} = {_copy_if_ref(args[0])}")
        elif op == "iinc":
            lv, delta = args
            w(f"{ind}{lv.name} += Int32({delta})")
        elif op == "binop":
            _emit_binop(w, ind, args[0], dst, args[1], args[2])
        elif op == "unop":
            _emit_unop(w, ind, args[0], dst, args[1])
        elif op == "new":
            cname = args[0]
            if cname == "java/lang/StringBuilder":
                w(f"{ind}{dst.name} = new_sb()")
            elif cname in hierarchy.classes and not hierarchy.classes[cname].cf.is_interface:
                mc = mangle_class(cname)
                is_cross = (
                    stats.current_shard is not None
                    and stats.class_to_shard is not None
                    and stats.class_to_shard.get(cname) != stats.current_shard
                )
                if is_cross:
                    xsym = f"x_new_arena_{mc}" if dst.name in opt.arena_allocs else f"x_new_{mc}"
                    w(f'{ind}external_call["{xsym}", NoneType](_s)')
                    w(f"{ind}if _s[].has_exc:")
                    w(f"{ind}    _s[].has_exc = False")
                    w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_new", _s[].exc_obj.copy())')
                    w(f"{ind}{dst.name} = _s[].ret_ref.copy()")
                    w(f"{ind}_s[].ret_ref = JRef()")
                elif dst.name in opt.arena_allocs:
                    w(f"{ind}{dst.name} = new_arena_{mc}(_s)")
                else:
                    w(f"{ind}{dst.name} = new_{mc}()")
            else:
                w(f"{ind}{dst.name} = JRef()")
        elif op == "newarray":
            atype, cnt = args
            elem_t = {"Z": "Bool", "B": "Int8", "C": "UInt16", "S": "Int16", "I": "Int32", "J": "Int64", "F": "Float32", "D": "Float64"}.get(atype, "JRef")
            cid_expr = {"Z": "CID_ARRAY_B1", "B": "CID_ARRAY_B1", "C": "CID_ARRAY_B2", "S": "CID_ARRAY_B2", "I": "CID_ARRAY_B4", "F": "CID_ARRAY_B4", "J": "CID_ARRAY_B8", "D": "CID_ARRAY_B8"}.get(atype, "CID_ARRAY_REF")
            if dst.name in opt.arena_allocs:
                w(f"{ind}{dst.name} = new_array_arena[{elem_t}](_s[].arena, {cid_expr}, {cnt.name})")
            else:
                w(f"{ind}{dst.name} = new_array[{elem_t}]({cid_expr}, {cnt.name})")
        elif op == "multianewarray":
            cls_desc, dim_vals = args
            leaf_c = cls_desc.lstrip("[")[0] if cls_desc.lstrip("[") else "L"
            leaf_t = {"Z": "Bool", "B": "Int8", "C": "UInt16", "S": "Int16", "I": "Int32", "J": "Int64", "F": "Float32", "D": "Float64"}.get(leaf_c, "JRef")
            dim_list = f"_dims_{dst.name}"
            w(f"{ind}var {dim_list} = List[Int32]()")
            for dv in dim_vals:
                w(f"{ind}{dim_list}.append({dv.name})")
            w(f"{ind}{dst.name} = new_multi_array[{leaf_t}]({dim_list}, 0, 9)")
        elif op == "arraylength":
            w(f"{ind}{dst.name} = array_len[Int32]({args[0].name})")
        elif op == "aload_elem":
            kind_c, arr, idx = args
            elem_t = {"i": "Int32", "l": "Int64", "f": "Float32", "d": "Float64", "a": "JRef", "b": "Int8", "c": "UInt16", "s": "Int16"}[kind_c]
            cast_wrap = "" if elem_t in ("Int32", "Int64", "Float32", "Float64", "JRef") else "Int32("
            end_wrap = ")" if cast_wrap else ""
            w(f"{ind}{dst.name} = {cast_wrap}array_get[{elem_t}]({arr.name}, {idx.name}){end_wrap}")
        elif op == "astore_elem":
            kind_c, arr, idx, val = args
            elem_t = {"i": "Int32", "l": "Int64", "f": "Float32", "d": "Float64", "a": "JRef", "b": "Int8", "c": "UInt16", "s": "Int16"}[kind_c]
            vexpr = _copy_if_ref(val) if elem_t == "JRef" else f"{elem_t}({val.name})" if elem_t != "Int32" else val.name
            w(f"{ind}array_set[{elem_t}]({arr.name}, {idx.name}, {vexpr})")
        elif op == "getstatic":
            owner, fname, _ = args
            decl_owner = _resolve_static_owner(hierarchy, owner, fname)
            if owner == "java/lang/System" and fname in ("out", "err"):
                w(f"{ind}{dst.name} = JRef()")
            elif decl_owner is not None:
                is_cross = (
                    stats.current_shard is not None
                    and stats.class_to_shard is not None
                    and stats.class_to_shard.get(decl_owner) != stats.current_shard
                )
                sname = f"sf_{mangle_class(decl_owner)[2:]}_{safe_ident(fname)}"
                if is_cross:
                    sym = f"x_gs_{mangle_class(decl_owner)[2:]}_{safe_ident(fname)}"
                    if dst.kind == KIND_R:
                        w(f'{ind}external_call["{sym}", NoneType](_s)')
                        w(f"{ind}if _s[].has_exc:")
                        w(f"{ind}    _s[].has_exc = False")
                        w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_gs", _s[].exc_obj.copy())')
                        w(f"{ind}{dst.name} = _s[].ret_ref.copy()")
                        w(f"{ind}_s[].ret_ref = JRef()")
                    else:
                        mtype = kind_to_mojo(dst.kind)
                        w(f'{ind}var _xgs_{dst.name} = external_call["{sym}", {mtype}](_s)')
                        w(f"{ind}if _s[].has_exc:")
                        w(f"{ind}    _s[].has_exc = False")
                        w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_gs", _s[].exc_obj.copy())')
                        w(f"{ind}{dst.name} = _xgs_{dst.name}")
                else:
                    base = "_sf(_s)[]" if stats.current_shard is not None else "_s[]"
                    w(f"{ind}{dst.name} = {base}.{sname}" + (".copy()" if dst.kind == KIND_R else ""))
            else:
                w(f"{ind}{dst.name} = {kind_default(dst.kind)}")
        elif op == "putstatic":
            owner, fname, _, val = args
            decl_owner = _resolve_static_owner(hierarchy, owner, fname)
            if decl_owner is not None:
                is_cross = (
                    stats.current_shard is not None
                    and stats.class_to_shard is not None
                    and stats.class_to_shard.get(decl_owner) != stats.current_shard
                )
                sname = f"sf_{mangle_class(decl_owner)[2:]}_{safe_ident(fname)}"
                if is_cross:
                    sym = f"x_ps_{mangle_class(decl_owner)[2:]}_{safe_ident(fname)}"
                    arg_v = f"{val.name}.ptr" if val.kind == KIND_R else val.name
                    w(f'{ind}external_call["{sym}", NoneType](_s, {arg_v})')
                    w(f"{ind}if _s[].has_exc:")
                    w(f"{ind}    _s[].has_exc = False")
                    w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_ps", _s[].exc_obj.copy())')
                else:
                    base = "_sf(_s)[]" if stats.current_shard is not None else "_s[]"
                    w(f"{ind}{base}.{sname} = {_copy_if_ref(val)}")
        elif op == "getfield":
            owner, fname, fdesc, obj = args
            if owner in hierarchy.classes and not hierarchy.classes[owner].cf.is_interface:
                fl = hierarchy.classes[owner].field_by_name.get(fname)
                mname = safe_ident(fl.mojo_name if fl else fname)
                fk = jtype_to_kind(JType(fl.descriptor if fl else fdesc))
                is_cross = (
                    stats.current_shard is not None
                    and stats.class_to_shard is not None
                    and stats.class_to_shard.get(owner) != stats.current_shard
                )
                if is_cross:
                    sym = f"x_gf_{mangle_class(owner)[2:]}_{safe_ident(fname)}"
                    if fk == KIND_R:
                        w(f'{ind}external_call["{sym}", NoneType](_s, {obj.name}.ptr)')
                        w(f"{ind}if _s[].has_exc:")
                        w(f"{ind}    _s[].has_exc = False")
                        w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_gf", _s[].exc_obj.copy())')
                        w(f"{ind}{dst.name} = _s[].ret_ref.copy()")
                        w(f"{ind}_s[].ret_ref = JRef()")
                    else:
                        mtype = kind_to_mojo(fk)
                        w(f'{ind}var _xgf_{dst.name} = external_call["{sym}", {mtype}](_s, {obj.name}.ptr)')
                        w(f"{ind}if _s[].has_exc:")
                        w(f"{ind}    _s[].has_exc = False")
                        w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_gf", _s[].exc_obj.copy())')
                        w(f"{ind}{dst.name} = _xgf_{dst.name}")
                else:
                    struct_t = mangle_class(owner) + "Data"
                    sfx = ".copy()" if dst.kind == KIND_R else ""
                    w(f"{ind}{dst.name} = {obj.name}.non_null().unsafe_bitcast[{struct_t}]()[].f_{mname}{sfx}")
            elif dst is not None and dst.name in opt.live_vars:
                w(f"{ind}{dst.name} = {kind_default(dst.kind)}")
        elif op == "putfield":
            owner, fname, _, obj, val = args
            if owner in hierarchy.classes and not hierarchy.classes[owner].cf.is_interface:
                fl = hierarchy.classes[owner].field_by_name.get(fname)
                mname = safe_ident(fl.mojo_name if fl else fname)
                is_cross = (
                    stats.current_shard is not None
                    and stats.class_to_shard is not None
                    and stats.class_to_shard.get(owner) != stats.current_shard
                )
                if is_cross:
                    sym = f"x_pf_{mangle_class(owner)[2:]}_{safe_ident(fname)}"
                    arg_v = f"{val.name}.ptr" if val.kind == KIND_R else val.name
                    w(f'{ind}external_call["{sym}", NoneType](_s, {obj.name}.ptr, {arg_v})')
                    w(f"{ind}if _s[].has_exc:")
                    w(f"{ind}    _s[].has_exc = False")
                    w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_pf", _s[].exc_obj.copy())')
                else:
                    struct_t = mangle_class(owner) + "Data"
                    w(f"{ind}{obj.name}.non_null().unsafe_bitcast[{struct_t}]()[].f_{mname} = {_copy_if_ref(val)}")
        elif op == "instanceof":
            _emit_instanceof(w, ind, hierarchy, dst, args[0], args[1], stats)
        elif op == "checkcast":
            _emit_checkcast(w, ind, hierarchy, dst, args[0], args[1], stats)
        elif op == "call":
            _emit_call(w, ind, hierarchy, dst, args, opt, stats)
        elif op == "indy":
            if dst is not None:
                w(f"{ind}{dst.name} = {kind_default(dst.kind)}")
        elif op == "unsupported":
            w(f"{ind}pass  # unsupported opcode: {args[0]}")

    if not emit_term or blk.term is None:
        return

    top, _, targs = blk.term.op, blk.term.dst, blk.term.args
    if top == "ret":
        if not targs:
            w(f"{ind}return")
        else:
            w(f"{ind}return {_copy_if_ref(targs[0])}")
    elif top == "throw":
        w(f'{ind}raise JThrowable({targs[0].name}.class_id(), "athrow", {targs[0].name}.copy())')
    elif top == "goto":
        w(f"{ind}_pc = {targs[0]}")
        w(f"{ind}continue")
    elif top == "if1":
        w(f"{ind}if {_cond_expr(blk.term)}:")
        w(f"{ind}    _pc = {targs[2]}")
        w(f"{ind}else:")
        w(f"{ind}    _pc = {targs[3]}")
        w(f"{ind}continue")
    elif top == "if2":
        w(f"{ind}if {_cond_expr(blk.term)}:")
        w(f"{ind}    _pc = {targs[3]}")
        w(f"{ind}else:")
        w(f"{ind}    _pc = {targs[4]}")
        w(f"{ind}continue")
    elif top == "switch":
        v, default_pc, table = targs
        first_s = True
        for key, tgt in sorted(table.items()):
            skw = "if" if first_s else "elif"
            first_s = False
            w(f"{ind}{skw} {v.name} == {key}:")
            w(f"{ind}    _pc = {tgt}")
        w(f"{ind}else:")
        w(f"{ind}    _pc = {default_pc}")
        w(f"{ind}continue")


def _iface_cond_expr(cid_v: str, runs: list[tuple[int, int]]) -> str:
    return " or ".join(
        f"{cid_v} == Int32({lo})" if lo == hi else f"({cid_v} >= Int32({lo}) and {cid_v} <= Int32({hi}))"
        for lo, hi in runs
    )


def _emit_instanceof(
    w, ind: str, hierarchy: Hierarchy, dst: Val, target_cls: str, obj: Val, stats: CodegenStats | None = None
) -> None:
    if target_cls == "java/lang/String":
        w(f"{ind}{dst.name} = Int32(1) if {obj.name}.class_id() == Int32(5) else Int32(0)")
        return
    if target_cls in hierarchy.classes:
        tci = hierarchy.classes[target_cls]
        if tci.cf.is_interface:
            ids = hierarchy.interface_impl_ids.get(target_cls, [])
            if not ids:
                w(f"{ind}{dst.name} = Int32(0)")
            else:
                runs = compress_cid_runs(ids)
                if len(runs) > 16 and (stats is None or target_cls in stats.large_ifaces):
                    w(f"{ind}{dst.name} = Int32(1) if {_iface_helper_name(target_cls)}({obj.name}.class_id()) else Int32(0)")
                else:
                    cid_v = f"_icid_{dst.name}"
                    w(f"{ind}var {cid_v} = {obj.name}.class_id()")
                    w(f"{ind}{dst.name} = Int32(1) if ({_iface_cond_expr(cid_v, runs)}) else Int32(0)")
        else:
            w(f"{ind}{dst.name} = is_instance({obj.name}, Int32({tci.class_id}), Int32({tci.max_sub_id}))")
    else:
        w(f"{ind}{dst.name} = Int32(0)")


def _emit_checkcast(
    w, ind: str, hierarchy: Hierarchy, dst: Val, target_cls: str, obj: Val, stats: CodegenStats | None = None
) -> None:
    if target_cls in hierarchy.classes:
        tci = hierarchy.classes[target_cls]
        if tci.cf.is_interface:
            ids = hierarchy.interface_impl_ids.get(target_cls, [])
            cid_v = f"_ccid_{dst.name}"
            w(f"{ind}if not {obj.name}.is_null():")
            if not ids:
                w(f'{ind}    raise JThrowable(6, "java/lang/ClassCastException")')
            else:
                runs = compress_cid_runs(ids)
                if len(runs) > 16 and (stats is None or target_cls in stats.large_ifaces):
                    w(f"{ind}    if not {_iface_helper_name(target_cls)}({obj.name}.class_id()):")
                    w(f'{ind}        raise JThrowable(6, "java/lang/ClassCastException")')
                else:
                    w(f"{ind}    var {cid_v} = {obj.name}.class_id()")
                    w(f"{ind}    if not ({_iface_cond_expr(cid_v, runs)}):")
                    w(f'{ind}        raise JThrowable(6, "java/lang/ClassCastException")')
            w(f"{ind}{dst.name} = {obj.name}.copy()")
        else:
            w(f"{ind}{dst.name} = check_cast({obj.name}, Int32({tci.class_id}), Int32({tci.max_sub_id}))")
    else:
        w(f"{ind}{dst.name} = {obj.name}.copy()")


def _emit_binop(w, ind: str, op: str, dst: Val, lhs: Val, rhs: Val) -> None:
    if op == "idiv":
        w(f"{ind}{dst.name} = idiv_j({lhs.name}, {rhs.name})")
        return
    if op == "irem":
        w(f"{ind}{dst.name} = irem_j({lhs.name}, {rhs.name})")
        return
    if op == "ldiv":
        w(f"{ind}{dst.name} = ldiv_j({lhs.name}, {rhs.name})")
        return
    if op == "lrem":
        w(f"{ind}{dst.name} = lrem_j({lhs.name}, {rhs.name})")
        return
    sym = {
        "iadd": "+", "ladd": "+", "fadd": "+", "dadd": "+",
        "isub": "-", "lsub": "-", "fsub": "-", "dsub": "-",
        "imul": "*", "lmul": "*", "fmul": "*", "dmul": "*",
        "fdiv": "/", "ddiv": "/", "frem": "%", "drem": "%",
        "ishl": "<<", "lshl": "<<", "ishr": ">>", "lshr": ">>",
        "iand": "&", "land": "&", "ior": "|", "lor": "|", "ixor": "^", "lxor": "^",
    }.get(op)
    if sym:
        if op in ("ishl", "ishr"):
            w(f"{ind}{dst.name} = {lhs.name} {sym} ({rhs.name} & 31)")
        elif op in ("lshl", "lshr"):
            w(f"{ind}{dst.name} = {lhs.name} {sym} Int64({rhs.name} & 63)")
        else:
            w(f"{ind}{dst.name} = {lhs.name} {sym} {rhs.name}")
    elif op == "iushr":
        w(f"{ind}{dst.name} = Int32(UInt32({lhs.name}) >> UInt32({rhs.name} & 31))")
    elif op == "lushr":
        w(f"{ind}{dst.name} = Int64(UInt64({lhs.name}) >> UInt64({rhs.name} & 63))")
    elif op == "lcmp":
        w(f"{ind}{dst.name} = Int32(1) if {lhs.name} > {rhs.name} else (Int32(-1) if {lhs.name} < {rhs.name} else Int32(0))")
    elif op in ("fcmpl", "fcmpg"):
        nan_v = -1 if op == "fcmpl" else 1
        w(f"{ind}{dst.name} = fcmp_j({lhs.name}, {rhs.name}, {nan_v})")
    elif op in ("dcmpl", "dcmpg"):
        nan_v = -1 if op == "dcmpl" else 1
        w(f"{ind}{dst.name} = dcmp_j({lhs.name}, {rhs.name}, {nan_v})")


def _emit_unop(w, ind: str, op: str, dst: Val, v: Val) -> None:
    if op.endswith("neg"):
        w(f"{ind}{dst.name} = -{v.name}")
    elif op in ("i2l", "f2l", "d2l"):
        w(f"{ind}{dst.name} = f2l_j({v.name})" if op == "f2l" else (f"{ind}{dst.name} = d2l_j({v.name})" if op == "d2l" else f"{ind}{dst.name} = Int64({v.name})"))
    elif op in ("l2i", "f2i", "d2i"):
        w(f"{ind}{dst.name} = f2i_j({v.name})" if op == "f2i" else (f"{ind}{dst.name} = d2i_j({v.name})" if op == "d2i" else f"{ind}{dst.name} = Int32({v.name})"))
    elif op in ("i2f", "l2f", "d2f"):
        w(f"{ind}{dst.name} = Float32({v.name})")
    elif op in ("i2d", "l2d", "f2d"):
        w(f"{ind}{dst.name} = Float64({v.name})")
    elif op == "i2b":
        w(f"{ind}{dst.name} = Int32(Int8({v.name}))")
    elif op == "i2c":
        w(f"{ind}{dst.name} = Int32(UInt16({v.name}))")
    elif op == "i2s":
        w(f"{ind}{dst.name} = Int32(Int16({v.name}))")


def _emit_call(w, ind: str, hierarchy: Hierarchy, dst: Val | None, args: tuple, opt: OptInfo, stats: CodegenStats) -> None:
    cop, owner, mname, mdesc, recv, cargs = args
    builtin = BUILTIN_CALLS.get((owner, mname, mdesc))

    def assign(expr: str) -> None:
        if dst is not None and dst.name in opt.live_vars:
            w(f"{ind}{dst.name} = {expr}")
        else:
            w(f"{ind}_ = {expr}")

    if builtin == "noop":
        return
    if builtin == "obj_hash":
        assign(f"{recv.name}.identity_hash()")
        return
    if builtin == "obj_eq":
        assign(f"Int32(1) if {recv.name}.same({cargs[0].name}) else Int32(0)")
        return
    if builtin == "println_empty":
        w(f"{ind}print()")
        return
    if builtin == "println_val":
        w(f"{ind}print({cargs[0].name})")
        return
    if builtin == "println_bool":
        w(f'{ind}print("true" if {cargs[0].name} != 0 else "false")')
        return
    if builtin == "println_char":
        w(f"{ind}print(chr(Int({cargs[0].name})))")
        return
    if builtin == "println_str":
        w(f"{ind}print(string_value({cargs[0].name}))")
        return
    if builtin == "sb_init":
        return
    if builtin == "sb_init_str":
        w(f"{ind}_ = sb_append_str({recv.name}, {cargs[0].name})")
        return
    if builtin == "sb_app_str":
        assign(f"sb_append_str({recv.name}, {cargs[0].name})")
        return
    if builtin == "sb_app_val":
        assign(f"sb_append_val({recv.name}, {cargs[0].name})")
        return
    if builtin == "sb_app_bool":
        assign(f"sb_append_bool({recv.name}, {cargs[0].name})")
        return
    if builtin == "sb_app_char":
        assign(f"sb_append_char({recv.name}, {cargs[0].name})")
        return
    if builtin == "sb_to_str":
        assign(f"sb_to_string({recv.name})")
        return
    if builtin == "str_eq":
        assign(f"string_equals({recv.name}, {cargs[0].name})")
        return
    if builtin == "str_hash":
        assign(f"string_hash({recv.name})")
        return
    if builtin == "str_len":
        assign(f"string_length({recv.name})")
        return
    if builtin == "str_char_at":
        assign(f"string_char_at({recv.name}, {cargs[0].name})")
        return
    if builtin == "str_concat":
        assign(f"string_concat({recv.name}, {cargs[0].name})")
        return
    if builtin == "str_sub1":
        assign(f"string_substring({recv.name}, {cargs[0].name})")
        return
    if builtin == "str_sub2":
        assign(f"string_substring({recv.name}, {cargs[0].name}, {cargs[1].name})")
        return
    if builtin == "int_to_str":
        assign(f"jint_to_string({cargs[0].name})")
        return
    if builtin == "long_to_str":
        assign(f"jlong_to_string({cargs[0].name})")
        return
    if builtin == "int_parse":
        assign(f"jint_parse({cargs[0].name})")
        return
    if builtin == "int_bitcount":
        assign(f"jint_bit_count({cargs[0].name})")
        return
    if builtin == "int_clz":
        assign(f"jint_clz({cargs[0].name})")
        return
    if builtin == "int_ctz":
        assign(f"jint_ctz({cargs[0].name})")
        return
    if builtin == "long_bitcount":
        assign(f"jlong_bit_count({cargs[0].name})")
        return
    if builtin == "long_clz":
        assign(f"jlong_clz({cargs[0].name})")
        return
    if builtin == "long_hash":
        assign(f"Int32({cargs[0].name} ^ ({cargs[0].name} >> 32))")
        return
    if builtin == "id_arg0":
        assign(cargs[0].name)
        return
    if builtin == "id_arg0_ref":
        w(f"{ind}_ = {cargs[0].name}.non_null()")
        assign(f"{cargs[0].name}.copy()")
        return
    if builtin == "abs_i":
        assign(f"jmath_abs_i({cargs[0].name})")
        return
    if builtin == "abs_l":
        assign(f"jmath_abs_l({cargs[0].name})")
        return
    if builtin == "abs_f":
        assign(f"jmath_abs_f({cargs[0].name})")
        return
    if builtin == "abs_d":
        assign(f"jmath_abs_d({cargs[0].name})")
        return
    if builtin == "min_i":
        assign(f"jmath_min_i({cargs[0].name}, {cargs[1].name})")
        return
    if builtin == "max_i":
        assign(f"jmath_max_i({cargs[0].name}, {cargs[1].name})")
        return
    if builtin == "min_f":
        assign(f"jmath_min_f({cargs[0].name}, {cargs[1].name})")
        return
    if builtin == "max_f":
        assign(f"jmath_max_f({cargs[0].name}, {cargs[1].name})")
        return
    if builtin == "min_d":
        assign(f"jmath_min_d({cargs[0].name}, {cargs[1].name})")
        return
    if builtin == "max_d":
        assign(f"jmath_max_d({cargs[0].name}, {cargs[1].name})")
        return
    if builtin == "math_sin":
        assign(f"jmath_sin({cargs[0].name})")
        return
    if builtin == "math_cos":
        assign(f"jmath_cos({cargs[0].name})")
        return
    if builtin == "math_tan":
        assign(f"jmath_tan({cargs[0].name})")
        return
    if builtin == "math_asin":
        assign(f"jmath_asin({cargs[0].name})")
        return
    if builtin == "math_acos":
        assign(f"jmath_acos({cargs[0].name})")
        return
    if builtin == "math_atan":
        assign(f"jmath_atan({cargs[0].name})")
        return
    if builtin == "math_sqrt":
        assign(f"jmath_sqrt({cargs[0].name})")
        return
    if builtin == "math_cbrt":
        assign(f"jmath_cbrt({cargs[0].name})")
        return
    if builtin == "math_exp":
        assign(f"jmath_exp({cargs[0].name})")
        return
    if builtin == "math_log":
        assign(f"jmath_log({cargs[0].name})")
        return
    if builtin == "math_log10":
        assign(f"jmath_log10({cargs[0].name})")
        return
    if builtin == "math_pow":
        assign(f"jmath_pow({cargs[0].name}, {cargs[1].name})")
        return
    if builtin == "math_hypot":
        assign(f"jmath_hypot({cargs[0].name}, {cargs[1].name})")
        return
    if builtin == "math_floor":
        assign(f"jmath_floor({cargs[0].name})")
        return
    if builtin == "math_ceil":
        assign(f"jmath_ceil({cargs[0].name})")
        return
    if builtin == "math_round_d":
        assign(f"jmath_round_d({cargs[0].name})")
        return
    if builtin == "math_round_f":
        assign(f"jmath_round_f({cargs[0].name})")
        return
    if builtin == "math_signum_d":
        assign(f"jmath_signum_d({cargs[0].name})")
        return
    if builtin == "math_signum_f":
        assign(f"jmath_signum_f({cargs[0].name})")
        return
    if builtin == "math_atan2":
        assign(f"jmath_atan2({cargs[0].name}, {cargs[1].name})")
        return
    if builtin == "float_to_bits":
        assign(f"jfloat_to_bits({cargs[0].name})")
        return
    if builtin == "bits_to_float":
        assign(f"jbits_to_float({cargs[0].name})")
        return
    if builtin == "double_to_bits":
        assign(f"jdouble_to_bits({cargs[0].name})")
        return
    if builtin == "bits_to_double":
        assign(f"jbits_to_double({cargs[0].name})")
        return
    if builtin == "float_isnan":
        assign(f"jfloat_isnan({cargs[0].name})")
        return
    if builtin == "float_isinf":
        assign(f"jfloat_isinf({cargs[0].name})")
        return
    if builtin == "double_isnan":
        assign(f"jdouble_isnan({cargs[0].name})")
        return
    if builtin == "double_isinf":
        assign(f"jdouble_isinf({cargs[0].name})")
        return
    if builtin == "objects_eq":
        assign(f"Int32(1) if {cargs[0].name}.same({cargs[1].name}) else (Int32(0) if ({cargs[0].name}.is_null() or {cargs[1].name}.is_null()) else string_equals({cargs[0].name}, {cargs[1].name}))")
        return
    if builtin == "objects_hash":
        assign(f"Int32(0) if {cargs[0].name}.is_null() else ({cargs[0].name}.identity_hash())")
        return
    if builtin == "sys_nano":
        assign("jsys_nano_time()")
        return
    if builtin == "sys_millis":
        assign("jsys_current_millis()")
        return
    if builtin == "sys_arraycopy":
        w(f"{ind}arraycopy_any({cargs[0].name}, {cargs[1].name}, {cargs[2].name}, {cargs[3].name}, {cargs[4].name})")
        return
    if builtin == "rt_cpus":
        assign("jruntime_cpus()")
        return
    if builtin == "thread_sleep":
        w(f"{ind}jthread_sleep({cargs[0].name})")
        return
    if builtin == "arrays_fill_i":
        w(f"{ind}jarrays_fill_i({cargs[0].name}, {cargs[1].name})")
        return
    if builtin == "unsafe_alloc":
        assign(f"junsafe_alloc({cargs[0].name})")
        return
    if builtin == "unsafe_realloc":
        assign(f"junsafe_realloc({cargs[0].name}, {cargs[1].name})")
        return
    if builtin == "unsafe_free":
        w(f"{ind}junsafe_free({cargs[0].name})")
        return
    if builtin == "unsafe_memset":
        w(f"{ind}junsafe_memset({cargs[0].name}, {cargs[1].name}, {cargs[2].name})")
        return
    if builtin == "unsafe_memcpy":
        w(f"{ind}junsafe_memcpy({cargs[0].name}, {cargs[1].name}, {cargs[2].name})")
        return
    if builtin in ("unsafe_get_i8", "unsafe_get_i16", "unsafe_get_i32", "unsafe_get_i64", "unsafe_get_f32", "unsafe_get_f64"):
        assign(f"j{builtin}({cargs[0].name})")
        return
    if builtin in ("unsafe_put_i8", "unsafe_put_i16", "unsafe_put_i32", "unsafe_put_i64", "unsafe_put_f32", "unsafe_put_f64"):
        w(f"{ind}j{builtin}({cargs[0].name}, {cargs[1].name})")
        return

    target_owner = owner
    if cop in ("invokevirtual", "invokeinterface"):
        devirt = hierarchy.resolve_devirtualized(owner, mname, mdesc)
        if devirt is not None:
            target_owner = devirt
            stats.calls_devirtualized += 1
        else:
            stats.calls_virtual += 1
            sub_impls = hierarchy.concrete_targets(owner, mname, mdesc)
            if sub_impls:
                rhs_dst = dst if (dst is not None and dst.name in opt.live_vars) else None
                if len(sub_impls) <= 8:
                    cid_var = f"_cid_{recv.name}_{id(args) & 0xFFFF}"
                    w(f"{ind}var {cid_var} = {recv.name}.class_id()")
                    for idx_s, (sub_cid, impl_owner) in enumerate(sub_impls):
                        skw = "if" if idx_s == 0 else "elif"
                        w(f"{ind}{skw} {cid_var} == {sub_cid}:")
                        _emit_resolved_call_or_builtin(w, ind + "    ", hierarchy, stats, impl_owner, mname, mdesc, recv, cargs, rhs_dst)
                    w(f"{ind}else:")
                    w(f'{ind}    raise JThrowable(8, "AbstractMethodError: {owner}.{mname}")')
                else:
                    vkey = (owner, mname, mdesc)
                    stats.vdisp_needed.add(vkey)
                    _emit_vdisp_call(w, ind, stats, vkey, recv, cargs, rhs_dst)
                return

    resolved_owner = hierarchy.resolve_method(target_owner, mname, mdesc)
    if resolved_owner is None:
        if dst is not None and dst.name in opt.live_vars:
            w(f"{ind}{dst.name} = {kind_default(dst.kind)}  # external: {owner}.{mname}{mdesc}")
        else:
            w(f"{ind}pass  # external: {owner}.{mname}{mdesc}")
        return

    rhs_dst = dst if (dst is not None and dst.name in opt.live_vars) else None
    _emit_resolved_call(w, ind, stats, resolved_owner, mname, mdesc, recv, cargs, rhs_dst)


def _vdisp_fn_name(owner: str, mname: str, mdesc: str) -> str:
    return "vd_" + mangle_method(owner, mname, mdesc)


def _emit_resolved_call_or_builtin(
    w,
    ind: str,
    hierarchy: Hierarchy,
    stats: CodegenStats,
    impl_owner: str,
    mname: str,
    mdesc: str,
    recv: Val,
    cargs: tuple,
    dst: Val | None,
) -> None:
    if (impl_owner, mname, mdesc) == ("java/lang/Object", "hashCode", "()I"):
        if dst is not None:
            w(f"{ind}{dst.name} = {recv.name}.identity_hash()")
        else:
            w(f"{ind}_ = {recv.name}.identity_hash()")
        return
    if (impl_owner, mname, mdesc) == ("java/lang/Object", "equals", "(Ljava/lang/Object;)Z"):
        expr = f"Int32(1) if {recv.name}.same({cargs[0].name}) else Int32(0)"
        if dst is not None:
            w(f"{ind}{dst.name} = {expr}")
        else:
            w(f"{ind}_ = {expr}")
        return
    if hierarchy.resolve_method(impl_owner, mname, mdesc) is None:
        if dst is not None:
            w(f"{ind}{dst.name} = {kind_default(dst.kind)}  # native/ext: {impl_owner}.{mname}{mdesc}")
        else:
            w(f"{ind}pass  # native/ext: {impl_owner}.{mname}{mdesc}")
        return
    _emit_resolved_call(w, ind, stats, impl_owner, mname, mdesc, recv, cargs, dst)


def _emit_vdisp_call(
    w,
    ind: str,
    stats: CodegenStats,
    vkey: tuple[str, str, str],
    recv: Val,
    cargs: tuple,
    dst: Val | None,
) -> None:
    from .types import parse_method_desc

    owner, mname, mdesc = vkey
    vd_name = _vdisp_fn_name(owner, mname, mdesc)
    is_cross = (
        stats.current_shard is not None
        and stats.vdisp_to_shard is not None
        and stats.vdisp_to_shard.get(vkey) != stats.current_shard
    )
    if not is_cross:
        call_list = ["_s", recv.name, *(a.name for a in cargs)]
        expr = f"{vd_name}({', '.join(call_list)})"
        if dst is not None:
            w(f"{ind}{dst.name} = {expr}")
        else:
            w(f"{ind}{expr}")
        return

    _, ret_t = parse_method_desc(mdesc)
    if ret_t.is_void:
        c_ret = "NoneType"
    else:
        rk = jtype_to_kind(ret_t)
        c_ret = "NoneType" if rk == KIND_R else kind_to_mojo(rk)
    x_args = ["_s", f"{recv.name}.ptr"]
    for a in cargs:
        x_args.append(f"{a.name}.ptr" if a.kind == KIND_R else a.name)
    call_expr = f'external_call["x{vd_name}", {c_ret}]({", ".join(x_args)})'
    if dst is not None and not ret_t.is_void:
        if jtype_to_kind(ret_t) == KIND_R:
            w(f"{ind}{call_expr}")
            w(f"{ind}if _s[].has_exc:")
            w(f"{ind}    _s[].has_exc = False")
            w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_vd", _s[].exc_obj.copy())')
            w(f"{ind}{dst.name} = _s[].ret_ref.copy()")
            w(f"{ind}_s[].ret_ref = JRef()")
        else:
            w(f"{ind}var _xvd_{dst.name} = {call_expr}")
            w(f"{ind}if _s[].has_exc:")
            w(f"{ind}    _s[].has_exc = False")
            w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_vd", _s[].exc_obj.copy())')
            w(f"{ind}{dst.name} = _xvd_{dst.name}")
    else:
        w(f"{ind}{call_expr}")
        w(f"{ind}if _s[].has_exc:")
        w(f"{ind}    _s[].has_exc = False")
        w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_vd", _s[].exc_obj.copy())')
        if not ret_t.is_void and jtype_to_kind(ret_t) == KIND_R:
            w(f"{ind}_s[].ret_ref = JRef()")


def emit_vdisp_function(
    out: list[str],
    hierarchy: Hierarchy,
    owner: str,
    mname: str,
    mdesc: str,
    stats: CodegenStats,
    *,
    export_c_abi: bool = False,
) -> None:
    """Emit shared O(log N) balanced-binary-tree dispatcher for a polymorphic virtual method."""
    from collections import Counter

    from .types import parse_method_desc

    w = out.append
    sub_impls = hierarchy.concrete_targets(owner, mname, mdesc)
    param_types, ret_t = parse_method_desc(mdesc)
    vd_name = _vdisp_fn_name(owner, mname, mdesc)

    sig = ["_s: JStaticsPtr", "arg0: JRef"]
    fwd = ["_s", "arg0"]
    cargs_vals = []
    for i, pt in enumerate(param_types):
        k = jtype_to_kind(pt)
        pname = f"p{i}"
        sig.append(f"{pname}: {kind_to_mojo(k)}")
        fwd.append(pname)
        cargs_vals.append(Val(pname, k))

    ret_k = "V" if ret_t.is_void else jtype_to_kind(ret_t)
    ret_ann = "" if ret_t.is_void else f" -> {kind_to_mojo(ret_k)}"

    counts = Counter(impl for _, impl in sub_impls)
    default_impl: str | None = None
    if counts:
        top_impl, top_cnt = counts.most_common(1)[0]
        if top_cnt >= 4 or owner == "java/lang/Object":
            default_impl = top_impl

    # Build sorted contiguous (lo, hi, impl_owner) intervals excluding default_impl
    intervals: list[tuple[int, int, str]] = []
    for cid, impl in sub_impls:
        if impl == default_impl:
            continue
        if intervals and intervals[-1][2] == impl and intervals[-1][1] + 1 == cid:
            intervals[-1] = (intervals[-1][0], cid, impl)
        else:
            intervals.append((cid, cid, impl))

    def emit_leaf_return(ind: str, impl_owner: str | None) -> None:
        if impl_owner is None:
            w(f'{ind}raise JThrowable(8, "AbstractMethodError: {owner}.{mname}")')
            return
        if (impl_owner, mname, mdesc) == ("java/lang/Object", "hashCode", "()I"):
            w(f"{ind}return arg0.identity_hash()")
            return
        if (impl_owner, mname, mdesc) == ("java/lang/Object", "equals", "(Ljava/lang/Object;)Z"):
            w(f"{ind}return Int32(1) if arg0.same(p0) else Int32(0)")
            return
        if hierarchy.resolve_method(impl_owner, mname, mdesc) is None:
            w(f"{ind}return" if ret_t.is_void else f"{ind}return {kind_default(ret_k)}")
            return
        fn_m = mangle_method(impl_owner, mname, mdesc)
        is_cross = (
            stats.current_shard is not None
            and stats.class_to_shard is not None
            and stats.class_to_shard.get(impl_owner) != stats.current_shard
        )
        if not is_cross:
            expr = f"{fn_m}({', '.join(fwd)})"
            if ret_t.is_void:
                w(f"{ind}{expr}")
                w(f"{ind}return")
            else:
                w(f"{ind}return {expr}")
            return
        c_ret = "NoneType" if (ret_t.is_void or ret_k == KIND_R) else kind_to_mojo(ret_k)
        x_args = ["_s", "arg0.ptr"] + [f"{v.name}.ptr" if v.kind == KIND_R else v.name for v in cargs_vals]
        cexpr = f'external_call["x_{fn_m}", {c_ret}]({", ".join(x_args)})'
        if ret_t.is_void:
            w(f"{ind}{cexpr}")
            w(f"{ind}if _s[].has_exc:")
            w(f"{ind}    _s[].has_exc = False")
            w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_shard", _s[].exc_obj.copy())')
            w(f"{ind}return")
        elif ret_k == KIND_R:
            w(f"{ind}{cexpr}")
            w(f"{ind}if _s[].has_exc:")
            w(f"{ind}    _s[].has_exc = False")
            w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_shard", _s[].exc_obj.copy())')
            w(f"{ind}var _r = _s[].ret_ref.copy()")
            w(f"{ind}_s[].ret_ref = JRef()")
            w(f"{ind}return _r")
        else:
            w(f"{ind}var _xr = {cexpr}")
            w(f"{ind}if _s[].has_exc:")
            w(f"{ind}    _s[].has_exc = False")
            w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_shard", _s[].exc_obj.copy())')
            w(f"{ind}return _xr")

    def emit_btree(ind: str, ivs: list[tuple[int, int, str]]) -> None:
        if not ivs:
            emit_leaf_return(ind, default_impl)
            return
        if len(ivs) <= 4:
            for idx_i, (lo, hi, imp) in enumerate(ivs):
                kw = "if" if idx_i == 0 else "elif"
                cond = f"_cid == Int32({lo})" if lo == hi else f"(_cid >= Int32({lo}) and _cid <= Int32({hi}))"
                w(f"{ind}{kw} {cond}:")
                emit_leaf_return(ind + "    ", imp)
            w(f"{ind}else:")
            emit_leaf_return(ind + "    ", default_impl)
            return
        mid = len(ivs) // 2
        w(f"{ind}if _cid < Int32({ivs[mid][0]}):")
        emit_btree(ind + "    ", ivs[:mid])
        w(f"{ind}else:")
        emit_btree(ind + "    ", ivs[mid:])

    # Chunk large interval tables (>32 intervals) so every Mojo function stays tiny
    chunk_size = 32
    if len(intervals) <= chunk_size:
        w(f"def {vd_name}({', '.join(sig)}) raises JThrowable{ret_ann}:")
        w("    var _cid = arg0.class_id()")
        emit_btree("    ", intervals)
        w("")
    else:
        chunks = [intervals[i : i + chunk_size] for i in range(0, len(intervals), chunk_size)]
        chunk_sig = ["_s: JStaticsPtr", "_cid: Int32", *sig[1:]]
        for c_idx, ch in enumerate(chunks):
            w(f"def _{vd_name}_c{c_idx}({', '.join(chunk_sig)}) raises JThrowable{ret_ann}:")
            emit_btree("    ", ch)
            w("")

        def emit_chunk_tree(ind: str, c_lo: int, c_hi: int) -> None:
            if c_lo == c_hi:
                call_expr = f"_{vd_name}_c{c_lo}(_s, _cid, {', '.join(fwd[1:])})"
                if ret_t.is_void:
                    w(f"{ind}{call_expr}")
                    w(f"{ind}return")
                else:
                    w(f"{ind}return {call_expr}")
                return
            mid = (c_lo + c_hi + 1) // 2
            mid_cid = chunks[mid][0][0]
            w(f"{ind}if _cid < Int32({mid_cid}):")
            emit_chunk_tree(ind + "    ", c_lo, mid - 1)
            w(f"{ind}else:")
            emit_chunk_tree(ind + "    ", mid, c_hi)

        w(f"def {vd_name}({', '.join(sig)}) raises JThrowable{ret_ann}:")
        w("    var _cid = arg0.class_id()")
        emit_chunk_tree("    ", 0, len(chunks) - 1)
        w("")

    if export_c_abi:
        xsig = ["_s: JStaticsPtr", "raw_arg0: OptionalPointer[ObjHeader, MutUntrackedOrigin]"]
        xfwd = ["_s", "jref_from_raw(raw_arg0)"]
        for i, pt in enumerate(param_types):
            k = jtype_to_kind(pt)
            if k == KIND_R:
                xsig.append(f"raw_p{i}: OptionalPointer[ObjHeader, MutUntrackedOrigin]")
                xfwd.append(f"jref_from_raw(raw_p{i})")
            else:
                xsig.append(f"p{i}: {kind_to_mojo(k)}")
                xfwd.append(f"p{i}")
        c_ret = "NoneType" if (ret_t.is_void or ret_k == KIND_R) else kind_to_mojo(ret_k)
        w(f'@export("x{vd_name}")')
        w(f'def _x{vd_name}({", ".join(xsig)}) abi("C") -> {c_ret}:')
        w("    try:")
        inner = f"{vd_name}({', '.join(xfwd)})"
        if ret_t.is_void:
            w(f"        {inner}")
            w("        return")
        elif ret_k == KIND_R:
            w(f"        _s[].ret_ref = {inner}")
            w("        return")
        else:
            w(f"        return {inner}")
        w("    except _exc:")
        w("        _s[].has_exc = True")
        w("        _s[].exc_cid = _exc.class_id")
        w("        _s[].exc_obj = _exc.obj.copy()")
        if ret_t.is_void or ret_k == KIND_R:
            w("        return")
        else:
            w(f"        return {kind_default(ret_k)}")
        w("")


def _emit_resolved_call(
    w,
    ind: str,
    stats: CodegenStats,
    resolved_owner: str,
    mname: str,
    mdesc: str,
    recv: Val | None,
    cargs: tuple,
    dst: Val | None,
) -> None:
    fn_m = mangle_method(resolved_owner, mname, mdesc)
    is_cross = (
        stats.current_shard is not None
        and stats.class_to_shard is not None
        and stats.class_to_shard.get(resolved_owner) != stats.current_shard
    )
    if not is_cross:
        call_list = ["_s"]
        if recv is not None:
            call_list.append(recv.name)
        call_list.extend(a.name for a in cargs)
        call_expr = f"{fn_m}({', '.join(call_list)})"
        if dst is not None:
            w(f"{ind}{dst.name} = {call_expr}")
        else:
            w(f"{ind}{call_expr}")
        return

    # Cross-shard C-ABI external_call + exception propagation via _s
    from .types import parse_method_desc

    _, ret_t = parse_method_desc(mdesc)
    if ret_t.is_void:
        c_ret = "NoneType"
    else:
        rk = jtype_to_kind(ret_t)
        c_ret = "NoneType" if rk == KIND_R else kind_to_mojo(rk)

    x_args = ["_s"]
    if recv is not None:
        x_args.append(f"{recv.name}.ptr")
    for a in cargs:
        x_args.append(f"{a.name}.ptr" if a.kind == KIND_R else a.name)

    call_expr = f'external_call["x_{fn_m}", {c_ret}]({", ".join(x_args)})'
    if dst is not None and not ret_t.is_void:
        if jtype_to_kind(ret_t) == KIND_R:
            w(f"{ind}{call_expr}")
            w(f"{ind}if _s[].has_exc:")
            w(f"{ind}    _s[].has_exc = False")
            w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_shard", _s[].exc_obj.copy())')
            w(f"{ind}{dst.name} = _s[].ret_ref.copy()")
            w(f"{ind}_s[].ret_ref = JRef()")
        else:
            w(f"{ind}var _xr_{dst.name} = {call_expr}")
            w(f"{ind}if _s[].has_exc:")
            w(f"{ind}    _s[].has_exc = False")
            w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_shard", _s[].exc_obj.copy())')
            w(f"{ind}{dst.name} = _xr_{dst.name}")
    else:
        w(f"{ind}{call_expr}")
        w(f"{ind}if _s[].has_exc:")
        w(f"{ind}    _s[].has_exc = False")
        w(f'{ind}    raise JThrowable(_s[].exc_cid, "cross_shard", _s[].exc_obj.copy())')
        if not ret_t.is_void and jtype_to_kind(ret_t) == KIND_R:
            w(f"{ind}_s[].ret_ref = JRef()")


def emit_cross_trampoline(out: list[str], ir: MethodIR) -> None:
    """Emit `@export("x_<fn>") def _x_<fn>(...) abi("C")` wrapper for cross-shard callers."""
    w = out.append
    fn_m = mangle_method(ir.owner, ir.name, ir.descriptor)
    sig = ["_s: JStaticsPtr"]
    fwd = ["_s"]
    if not ir.is_static:
        sig.append("raw_arg0: OptionalPointer[ObjHeader, MutUntrackedOrigin]")
        fwd.append("jref_from_raw(raw_arg0)")
    for i, pt in enumerate(ir.params):
        k = jtype_to_kind(pt)
        if k == KIND_R:
            sig.append(f"raw_p{i}: OptionalPointer[ObjHeader, MutUntrackedOrigin]")
            fwd.append(f"jref_from_raw(raw_p{i})")
        else:
            sig.append(f"p{i}: {kind_to_mojo(k)}")
            fwd.append(f"p{i}")

    if ir.ret_type.is_void:
        c_ret = "NoneType"
        ret_k = "V"
    else:
        ret_k = jtype_to_kind(ir.ret_type)
        c_ret = "NoneType" if ret_k == KIND_R else kind_to_mojo(ret_k)

    w(f'@export("x_{fn_m}")')
    w(f'def _x_{fn_m}({", ".join(sig)}) abi("C") -> {c_ret}:')
    w("    try:")
    inner = f"{fn_m}({', '.join(fwd)})"
    if ir.ret_type.is_void:
        w(f"        {inner}")
        w("        return")
    elif ret_k == KIND_R:
        w(f"        _s[].ret_ref = {inner}")
        w("        return")
    else:
        w(f"        return {inner}")
    w("    except _exc:")
    w("        _s[].has_exc = True")
    w("        _s[].exc_cid = _exc.class_id")
    w("        _s[].exc_obj = _exc.obj.copy()")
    if ir.ret_type.is_void or ret_k == KIND_R:
        w("        return")
    else:
        w(f"        return {kind_default(ret_k)}")
    w("")


def _apply_param_aliases(ir: MethodIR, aliases: dict[str, tuple[str, str]]) -> None:
    from .optimize import _replace_reads

    repl = {k: Val(vname, vkind) for k, (vname, vkind) in aliases.items()}
    for blk in ir.blocks.values():
        for st in blk.stmts:
            _replace_reads(st, repl)
        if blk.term is not None:
            _replace_reads(blk.term, repl)
