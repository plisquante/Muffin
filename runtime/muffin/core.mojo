"""
Muffin Core Runtime (Mojo 1.2) — Zero-GC Object Model, ARC + Arena Fast Path,
Arrays, Compact Strings, StringBuilder, Exceptions, and JVMS Exact Arithmetic.

Key improvements over traditional Java-to-native runtimes:
1. `FLAG_ARENA` / `FLAG_IMMORTAL` on `ObjHeader`: non-escaping objects placed on
   the `FrameArena` (via Escape Analysis) and string literals skip atomic
   reference counting (`fetch_add`/`fetch_sub`) completely.
2. No stop-the-world GC, no shadow-stack frames, no per-call safepoint polls.
3. Exact JVMS §2.11 semantics for `idiv`/`irem`/`f2i`/`d2l`/`fcmpl`/`fcmpg`.
"""

from std.atomic import Atomic
from std.ffi import external_call
from std.math import isnan
from std.memory import OptionalPointer, Pointer
from std.origin import MutUntrackedOrigin
from std.sys import size_of

from .arena import FrameArena

comptime DropFn = def(Pointer[ObjHeader, MutUntrackedOrigin]) thin -> None
comptime RawFn = def() thin -> None

comptime FLAG_ARENA: Int32 = 1
comptime FLAG_IMMORTAL: Int32 = 2

# Reserved class IDs (0..9); user/JDK classes start at 10
comptime CID_OBJECT: Int32 = 1
comptime CID_STRING: Int32 = 5
comptime CID_STRING_BUILDER: Int32 = 6
comptime CID_THROWABLE: Int32 = 7
comptime CID_ARRAY_PRIM: Int32 = 8
comptime CID_ARRAY_REF: Int32 = 9
comptime CID_ARRAY_B1: Int32 = 10
comptime CID_ARRAY_B2: Int32 = 11
comptime CID_ARRAY_B4: Int32 = 12
comptime CID_ARRAY_B8: Int32 = 13


@fieldwise_init
struct TypeInfo(Copyable, Movable):
    var class_id: Int32
    var super_id: Int32
    var name: StaticString


def _noop_drop(p: Pointer[ObjHeader, MutUntrackedOrigin]):
    pass


struct ObjHeader:
    """Compact 16-byte header at offset 0 of every heap or arena Java object.

    Layout:
      - rc (4B): Atomic[Int32] reference count
      - meta (4B): bits 0..23 = class_id (up to 16.7M classes), bits 24..31 = flags
      - drop (8B): DropFn pointer
    """

    var rc: Atomic[Int32]
    var meta: UInt32
    var drop: DropFn

    @always_inline
    def __init__(
        out self,
        class_id: Int32,
        vtable: OptionalPointer[RawFn, MutUntrackedOrigin],
        drop: DropFn,
        flags: Int32 = 0,
    ):
        self.rc = Atomic[Int32](1)
        self.meta = (UInt32(class_id) & 0x00FFFFFF) | (UInt32(flags) << 24)
        self.drop = drop

    @always_inline
    def __init__(
        out self,
        class_id: Int32,
        flags: Int32,
    ):
        self.rc = Atomic[Int32](1)
        self.meta = (UInt32(class_id) & 0x00FFFFFF) | (UInt32(flags) << 24)
        self.drop = _noop_drop

    @always_inline
    def class_id(self) -> Int32:
        return Int32(self.meta & 0x00FFFFFF)

    @always_inline
    def flags(self) -> Int32:
        return Int32(self.meta >> 24)


struct JRef(Copyable, Defaultable, Writable):
    """Nullable, zero-GC handle to a JVM object (ARC on heap, zero-cost on arena)."""

    var ptr: OptionalPointer[ObjHeader, MutUntrackedOrigin]

    @always_inline
    def __init__(out self):
        self.ptr = {}

    @always_inline
    def __init__(out self, raw: Pointer[ObjHeader, MutUntrackedOrigin]):
        self.ptr = raw

    @always_inline
    def __init__(out self, *, copy: Self):
        self.ptr = copy.ptr
        if self.ptr:
            var raw = self.ptr.unsafe_value()
            if (raw[].meta >> 24) == 0:
                _ = raw[].rc.fetch_add(1)

    @always_inline
    def __deinit__(deinit self):
        if self.ptr:
            var raw = self.ptr.unsafe_value()
            if (raw[].meta >> 24) == 0:
                if raw[].rc.fetch_sub(1) == 1:
                    raw[].drop(raw)

    def write_to(self, mut writer: Some[Writer]):
        if not self.ptr:
            writer.write("null")
        else:
            writer.write("JRef(class=", self.ptr.unsafe_value()[].class_id(), ")")

    @always_inline
    def is_null(self) -> Bool:
        return not Bool(self.ptr)

    @always_inline
    def non_null(self) raises JThrowable -> Pointer[ObjHeader, MutUntrackedOrigin]:
        if not self.ptr:
            raise JThrowable(1, "java/lang/NullPointerException")
        return self.ptr.unsafe_value()

    @always_inline
    def class_id(self) -> Int32:
        if not self.ptr:
            return -1
        return self.ptr.unsafe_value()[].class_id()

    @always_inline
    def same(self, other: Self) -> Bool:
        if not self.ptr and not other.ptr:
            return True
        if not self.ptr or not other.ptr:
            return False
        return Int(self.ptr.unsafe_value()) == Int(other.ptr.unsafe_value())

    @always_inline
    def identity_hash(self) -> Int32:
        if not self.ptr:
            return 0
        var addr = UInt64(Int(self.ptr.unsafe_value()))
        return Int32((addr >> 4) ^ (addr >> 32))


@always_inline
def jref_from_raw(raw: OptionalPointer[ObjHeader, MutUntrackedOrigin]) -> JRef:
    """Reconstructs an owned JRef from a raw ObjHeader pointer passed across C ABI."""
    if raw:
        var p = raw.unsafe_value()
        if (p[].meta >> 24) == 0:
            _ = p[].rc.fetch_add(1)
        return JRef(p)
    return JRef()


@fieldwise_init
struct JThrowable(Copyable, Movable, Writable):
    """Typed Java exception lowered to Mojo's zero-cost `raises` mechanism."""

    var class_id: Int32
    var message: String
    var obj: JRef

    def __init__(out self, class_id: Int32, message: String):
        self.class_id = class_id
        self.message = message
        self.obj = JRef()


@always_inline
def alloc_raw[T: AnyType](out ptr: Pointer[T, MutUntrackedOrigin]) raises JThrowable:
    var raw = external_call["malloc", OptionalPointer[T, MutUntrackedOrigin]](
        Int(size_of[T]())
    )
    if not raw:
        raise JThrowable(2, "java/lang/OutOfMemoryError")
    ptr = raw.unsafe_value()


@always_inline
def free_raw[T: AnyType](ptr: Pointer[T, MutUntrackedOrigin]):
    external_call["free", NoneType](ptr.unsafe_bitcast[NoneType]())


@always_inline
def alloc_arena_obj[T: AnyType](mut arena: FrameArena) -> Pointer[T, MutUntrackedOrigin]:
    """Zero-malloc, zero-RC object allocation on the thread's FrameArena."""
    return arena.alloc_bytes(Int(size_of[T]()), 8).unsafe_bitcast[T]()


# --------------------------------------------------------------------------- #
# Arrays
# --------------------------------------------------------------------------- #
struct JArrayData[T: Copyable & Deinitable]:
    var hdr: ObjHeader
    var length: Int32
    var data: OptionalPointer[Self.T, MutUntrackedOrigin]

    def __init__(out self, class_id: Int32, length: Int32, drop: DropFn) raises JThrowable:
        self.hdr = ObjHeader(class_id, {}, drop)
        self.length = length
        if length < 0:
            raise JThrowable(3, "java/lang/NegativeArraySizeException")
        if length == 0:
            self.data = {}
        else:
            var bytes = Int(length) * Int(size_of[Self.T]())
            var buf = external_call["malloc", OptionalPointer[Self.T, MutUntrackedOrigin]](bytes)
            if not buf:
                raise JThrowable(2, "java/lang/OutOfMemoryError")
            self.data = buf


def drop_array[T: Copyable & Deinitable & Defaultable](p: Pointer[ObjHeader, MutUntrackedOrigin]):
    var ap = p.unsafe_bitcast[JArrayData[T]]()
    if ap[].data:
        var buf = ap[].data.unsafe_value()
        for i in range(Int(ap[].length)):
            buf.unsafe_offset(i).unsafe_deinit_pointee()
        free_raw(buf)
    ap.unsafe_deinit_pointee()
    free_raw(ap)


def new_array[T: Copyable & Deinitable & Defaultable](class_id: Int32, length: Int32) raises JThrowable -> JRef:
    var raw = alloc_raw[JArrayData[T]]()
    raw.unsafe_write(JArrayData[T](class_id, length, drop_array[T]))
    if raw[].data:
        var buf = raw[].data.unsafe_value()
        for i in range(Int(length)):
            buf.unsafe_offset(i).unsafe_write(T())
    return JRef(raw.unsafe_bitcast[ObjHeader]())


def new_array_arena[T: Copyable & Deinitable & Defaultable](
    mut arena: FrameArena, class_id: Int32, length: Int32
) raises JThrowable -> JRef:
    """Allocates an array inside the bump arena (for non-escaping primitive/ref arrays)."""
    if length < 0:
        raise JThrowable(3, "java/lang/NegativeArraySizeException")
    var hdr_p = arena.alloc_bytes(Int(size_of[JArrayData[T]]()), 8).unsafe_bitcast[JArrayData[T]]()
    hdr_p[].hdr = ObjHeader(class_id, FLAG_ARENA)
    hdr_p[].length = length
    if length == 0:
        hdr_p[].data = {}
    else:
        var bytes = Int(length) * Int(size_of[T]())
        var buf = arena.alloc_bytes(bytes, 8).unsafe_bitcast[T]()
        for i in range(Int(length)):
            buf.unsafe_offset(i).unsafe_write(T())
        hdr_p[].data = buf
    return JRef(hdr_p.unsafe_bitcast[ObjHeader]())


def new_multi_array[LeafT: Copyable & Deinitable & Defaultable](
    dims: List[Int32], depth: Int, class_id: Int32
) raises JThrowable -> JRef:
    var n = dims[depth]
    if depth == len(dims) - 1:
        return new_array[LeafT](class_id, n)
    var outer = new_array[JRef](CID_ARRAY_REF, n)
    for i in range(Int(n)):
        array_set[JRef](outer, Int32(i), new_multi_array[LeafT](dims, depth + 1, class_id))
    return outer^


@always_inline
def array_len[T: Copyable & Deinitable](ref_obj: JRef) raises JThrowable -> Int32:
    var hdr = ref_obj.non_null()
    return hdr.unsafe_bitcast[JArrayData[T]]()[].length


@always_inline
def array_get[T: Copyable & Deinitable](ref_obj: JRef, idx: Int32) raises JThrowable -> T:
    var hdr = ref_obj.non_null()
    var ap = hdr.unsafe_bitcast[JArrayData[T]]()
    if UInt32(idx) >= UInt32(ap[].length):
        raise JThrowable(4, "java/lang/ArrayIndexOutOfBoundsException")
    return ap[].data.unsafe_value().unsafe_offset(Int(idx))[].copy()


@always_inline
def array_set[T: Copyable & Deinitable](ref_obj: JRef, idx: Int32, var val: T) raises JThrowable:
    var hdr = ref_obj.non_null()
    var ap = hdr.unsafe_bitcast[JArrayData[T]]()
    if UInt32(idx) >= UInt32(ap[].length):
        raise JThrowable(4, "java/lang/ArrayIndexOutOfBoundsException")
    var slot = ap[].data.unsafe_value().unsafe_offset(Int(idx))
    slot.unsafe_deinit_pointee()
    slot.unsafe_write(val^)


def arraycopy[T: Copyable & Deinitable](
    src: JRef, src_pos: Int32, dst: JRef, dst_pos: Int32, length: Int32
) raises JThrowable:
    var sl = array_len[T](src)
    var dl = array_len[T](dst)
    if src_pos < 0 or dst_pos < 0 or length < 0 or src_pos + length > sl or dst_pos + length > dl:
        raise JThrowable(4, "java/lang/ArrayIndexOutOfBoundsException")
    if length == 0:
        return
    if src.same(dst) and dst_pos > src_pos:
        for i in range(Int(length) - 1, -1, -1):
            array_set[T](dst, dst_pos + Int32(i), array_get[T](src, src_pos + Int32(i)))
    else:
        for i in range(Int(length)):
            array_set[T](dst, dst_pos + Int32(i), array_get[T](src, src_pos + Int32(i)))


def arraycopy_any(
    src: JRef, src_pos: Int32, dst: JRef, dst_pos: Int32, length: Int32
) raises JThrowable:
    var scid = src.class_id()
    if scid == CID_ARRAY_REF:
        arraycopy[JRef](src, src_pos, dst, dst_pos, length)
    elif scid == CID_ARRAY_B1:
        arraycopy[Int8](src, src_pos, dst, dst_pos, length)
    elif scid == CID_ARRAY_B2:
        arraycopy[Int16](src, src_pos, dst, dst_pos, length)
    elif scid == CID_ARRAY_B8:
        arraycopy[Int64](src, src_pos, dst, dst_pos, length)
    else:
        arraycopy[Int32](src, src_pos, dst, dst_pos, length)


# --------------------------------------------------------------------------- #
# Strings & StringBuilder (Compact UTF-8/Latin-1 representation)
# --------------------------------------------------------------------------- #
struct JStringData:
    var hdr: ObjHeader
    var value: String
    var cached_hash: Int32
    var hash_valid: Bool

    def __init__(out self, var value: String, drop: DropFn):
        self.hdr = ObjHeader(CID_STRING, {}, drop)
        self.value = value^
        self.cached_hash = 0
        self.hash_valid = False


def drop_string(p: Pointer[ObjHeader, MutUntrackedOrigin]):
    var sp = p.unsafe_bitcast[JStringData]()
    sp.unsafe_deinit_pointee()
    free_raw(sp)


def new_string(var value: String) raises JThrowable -> JRef:
    var raw = alloc_raw[JStringData]()
    raw.unsafe_write(JStringData(value^, drop_string))
    return JRef(raw.unsafe_bitcast[ObjHeader]())


def string_value(ref_obj: JRef) raises JThrowable -> String:
    if ref_obj.is_null():
        return "null"
    var hdr = ref_obj.non_null()
    return hdr.unsafe_bitcast[JStringData]()[].value


def string_length(ref_obj: JRef) raises JThrowable -> Int32:
    var hdr = ref_obj.non_null()
    return Int32(hdr.unsafe_bitcast[JStringData]()[].value.byte_length())


def string_equals(a: JRef, b: JRef) raises JThrowable -> Int32:
    if a.is_null():
        raise JThrowable(1, "java/lang/NullPointerException")
    if a.same(b):
        return 1
    if b.is_null() or b.class_id() != CID_STRING:
        return 0
    return Int32(1) if string_value(a) == string_value(b) else Int32(0)


def string_hash(ref_obj: JRef) raises JThrowable -> Int32:
    var hdr = ref_obj.non_null()
    var sp = hdr.unsafe_bitcast[JStringData]()
    if sp[].hash_valid:
        return sp[].cached_hash
    var h: Int32 = 0
    for b in sp[].value.as_bytes():
        h = h * 31 + Int32(b)
    sp[].cached_hash = h
    sp[].hash_valid = True
    return h


def string_char_at(ref_obj: JRef, idx: Int32) raises JThrowable -> Int32:
    var s = string_value(ref_obj)
    var bs = s.as_bytes()
    if idx < 0 or Int(idx) >= len(bs):
        raise JThrowable(4, "java/lang/StringIndexOutOfBoundsException")
    return Int32(bs[Int(idx)])


def string_concat(a: JRef, b: JRef) raises JThrowable -> JRef:
    return new_string(string_value(a) + string_value(b))


def string_substring(ref_obj: JRef, begin: Int32, end: Int32 = -1) raises JThrowable -> JRef:
    var s = string_value(ref_obj)
    var n = Int32(s.byte_length())
    var e = n if end == -1 else end
    if begin < 0 or e > n or begin > e:
        raise JThrowable(4, "java/lang/StringIndexOutOfBoundsException")
    return new_string(String(s[byte=Int(begin):Int(e)]))


struct JStringBuilderData:
    var hdr: ObjHeader
    var buf: String

    def __init__(out self, drop: DropFn):
        self.hdr = ObjHeader(CID_STRING_BUILDER, {}, drop)
        self.buf = String()


def drop_sb(p: Pointer[ObjHeader, MutUntrackedOrigin]):
    var sp = p.unsafe_bitcast[JStringBuilderData]()
    sp.unsafe_deinit_pointee()
    free_raw(sp)


def new_sb() raises JThrowable -> JRef:
    var raw = alloc_raw[JStringBuilderData]()
    raw.unsafe_write(JStringBuilderData(drop_sb))
    return JRef(raw.unsafe_bitcast[ObjHeader]())


def sb_append_str(sb: JRef, s: JRef) raises JThrowable -> JRef:
    var hdr = sb.non_null()
    hdr.unsafe_bitcast[JStringBuilderData]()[].buf += string_value(s)
    return sb.copy()


def sb_append_val[T: Writable](sb: JRef, val: T) raises JThrowable -> JRef:
    var hdr = sb.non_null()
    hdr.unsafe_bitcast[JStringBuilderData]()[].buf += String(val)
    return sb.copy()


def sb_append_bool(sb: JRef, val: Int32) raises JThrowable -> JRef:
    var hdr = sb.non_null()
    hdr.unsafe_bitcast[JStringBuilderData]()[].buf += ("true" if val != 0 else "false")
    return sb.copy()


def sb_append_char(sb: JRef, ch: Int32) raises JThrowable -> JRef:
    var hdr = sb.non_null()
    hdr.unsafe_bitcast[JStringBuilderData]()[].buf += chr(Int(ch))
    return sb.copy()


def sb_to_string(sb: JRef) raises JThrowable -> JRef:
    var hdr = sb.non_null()
    return new_string(hdr.unsafe_bitcast[JStringBuilderData]()[].buf)


# --------------------------------------------------------------------------- #
# Type checks (O(1) class interval + interface support)
# --------------------------------------------------------------------------- #
@always_inline
def is_instance(ref_obj: JRef, lo: Int32, hi: Int32) -> Int32:
    var cid = ref_obj.class_id()
    return Int32(1) if (cid >= lo and cid <= hi) else Int32(0)


@always_inline
def check_cast(ref_obj: JRef, lo: Int32, hi: Int32) raises JThrowable -> JRef:
    if not ref_obj.is_null():
        var cid = ref_obj.class_id()
        if cid < lo or cid > hi:
            raise JThrowable(6, "java/lang/ClassCastException")
    return ref_obj.copy()


# --------------------------------------------------------------------------- #
# JVMS Exact Arithmetic Helpers
# --------------------------------------------------------------------------- #
@always_inline
def idiv_j(a: Int32, b: Int32) raises JThrowable -> Int32:
    if b == 0:
        raise JThrowable(5, "java/lang/ArithmeticException")
    if a == -2147483648 and b == -1:
        return a
    var aa = abs(Int64(a))
    var bb = abs(Int64(b))
    var q = Int32(aa // bb)
    return -q if ((a < 0) ^ (b < 0)) else q


@always_inline
def irem_j(a: Int32, b: Int32) raises JThrowable -> Int32:
    if b == 0:
        raise JThrowable(5, "java/lang/ArithmeticException")
    if a == -2147483648 and b == -1:
        return 0
    var aa = abs(Int64(a))
    var bb = abs(Int64(b))
    var r = Int32(aa % bb)
    return -r if a < 0 else r


@always_inline
def ldiv_j(a: Int64, b: Int64) raises JThrowable -> Int64:
    if b == 0:
        raise JThrowable(5, "java/lang/ArithmeticException")
    if a == -9223372036854775808 and b == -1:
        return a
    var ua = (UInt64(0) - UInt64(a)) if a < 0 else UInt64(a)
    var ub = (UInt64(0) - UInt64(b)) if b < 0 else UInt64(b)
    var q = Int64(ua // ub)
    return -q if ((a < 0) ^ (b < 0)) else q


@always_inline
def lrem_j(a: Int64, b: Int64) raises JThrowable -> Int64:
    if b == 0:
        raise JThrowable(5, "java/lang/ArithmeticException")
    if a == -9223372036854775808 and b == -1:
        return 0
    var ua = (UInt64(0) - UInt64(a)) if a < 0 else UInt64(a)
    var ub = (UInt64(0) - UInt64(b)) if b < 0 else UInt64(b)
    var r = Int64(ua % ub)
    return -r if a < 0 else r


@always_inline
def f2i_j(v: Float32) -> Int32:
    if isnan(v):
        return 0
    if v >= 2147483648.0:
        return 2147483647
    if v <= -2147483648.0:
        return -2147483648
    return Int32(v)


@always_inline
def f2l_j(v: Float32) -> Int64:
    if isnan(v):
        return 0
    if v >= 9223372036854775808.0:
        return 9223372036854775807
    if v <= -9223372036854775808.0:
        return -9223372036854775808
    return Int64(v)


@always_inline
def d2i_j(v: Float64) -> Int32:
    if isnan(v):
        return 0
    if v >= 2147483647.0:
        return 2147483647
    if v <= -2147483648.0:
        return -2147483648
    return Int32(v)


@always_inline
def d2l_j(v: Float64) -> Int64:
    if isnan(v):
        return 0
    if v >= 9223372036854775808.0:
        return 9223372036854775807
    if v <= -9223372036854775808.0:
        return -9223372036854775808
    return Int64(v)


@always_inline
def fcmp_j(a: Float32, b: Float32, nan_res: Int32) -> Int32:
    if isnan(a) or isnan(b):
        return nan_res
    return Int32(1) if a > b else (Int32(-1) if a < b else Int32(0))


@always_inline
def dcmp_j(a: Float64, b: Float64, nan_res: Int32) -> Int32:
    if isnan(a) or isnan(b):
        return nan_res
    return Int32(1) if a > b else (Int32(-1) if a < b else Int32(0))
