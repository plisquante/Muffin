"""
Muffin JDK Fast-Path Intrinsics (Mojo 1.2).

Direct native / SIMD / zero-GC implementations for the hottest JDK methods
found in `client.jar` (`java.lang.Math`, `Integer`, `Long`, `Arrays`, `System`).
"""

from std.bit import bit_reverse, byte_swap, count_leading_zeros, count_trailing_zeros, pop_count
from std.ffi import c_int, external_call
from std.math import acos, asin, atan, atan2, cbrt, ceil, cos, exp, floor, hypot, isinf, isnan, log, log10, pow, sin, sqrt, tan
from std.memory import OptionalPointer, Pointer, bitcast
from std.origin import MutUntrackedOrigin
from std.sys.info import num_physical_cores
from std.time import perf_counter_ns, sleep

from .core import JArrayData, JRef, JThrowable, new_string, string_value


@always_inline
def jmath_sin(x: Float64) -> Float64:
    return sin(x)


@always_inline
def jmath_cos(x: Float64) -> Float64:
    return cos(x)


@always_inline
def jmath_tan(x: Float64) -> Float64:
    return tan(x)


@always_inline
def jmath_asin(x: Float64) -> Float64:
    return asin(x)


@always_inline
def jmath_acos(x: Float64) -> Float64:
    return acos(x)


@always_inline
def jmath_atan(x: Float64) -> Float64:
    return atan(x)


@always_inline
def jmath_sqrt(x: Float64) -> Float64:
    return sqrt(x)


@always_inline
def jmath_cbrt(x: Float64) -> Float64:
    return cbrt(x)


@always_inline
def jmath_exp(x: Float64) -> Float64:
    return exp(x)


@always_inline
def jmath_log(x: Float64) -> Float64:
    return log(x)


@always_inline
def jmath_log10(x: Float64) -> Float64:
    return log10(x)


@always_inline
def jmath_pow(a: Float64, b: Float64) -> Float64:
    return pow(a, b)


@always_inline
def jmath_hypot(a: Float64, b: Float64) -> Float64:
    return hypot(a, b)


@always_inline
def jmath_floor(x: Float64) -> Float64:
    return floor(x)


@always_inline
def jmath_ceil(x: Float64) -> Float64:
    return ceil(x)


@always_inline
def jmath_round_d(x: Float64) -> Int64:
    if isnan(x):
        return 0
    return Int64(floor(x + 0.5))


@always_inline
def jmath_round_f(x: Float32) -> Int32:
    if isnan(x):
        return 0
    return Int32(floor(x + Float32(0.5)))


@always_inline
def jmath_atan2(y: Float64, x: Float64) -> Float64:
    return atan2(y, x)


@always_inline
def jmath_signum_d(x: Float64) -> Float64:
    if isnan(x) or x == 0.0:
        return x
    return 1.0 if x > 0.0 else -1.0


@always_inline
def jmath_signum_f(x: Float32) -> Float32:
    if isnan(x) or x == 0.0:
        return x
    return Float32(1.0) if x > 0.0 else Float32(-1.0)


@always_inline
def jmath_abs_i(x: Int32) -> Int32:
    return -x if x < 0 else x


@always_inline
def jmath_abs_l(x: Int64) -> Int64:
    return -x if x < 0 else x


@always_inline
def jmath_abs_f(x: Float32) -> Float32:
    return -x if x < 0 else x


@always_inline
def jmath_abs_d(x: Float64) -> Float64:
    return -x if x < 0 else x


@always_inline
def jmath_min_i(a: Int32, b: Int32) -> Int32:
    return a if a <= b else b


@always_inline
def jmath_max_i(a: Int32, b: Int32) -> Int32:
    return a if a >= b else b


@always_inline
def jmath_min_f(a: Float32, b: Float32) -> Float32:
    return a if a <= b else b


@always_inline
def jmath_max_f(a: Float32, b: Float32) -> Float32:
    return a if a >= b else b


@always_inline
def jmath_min_d(a: Float64, b: Float64) -> Float64:
    return a if a <= b else b


@always_inline
def jmath_max_d(a: Float64, b: Float64) -> Float64:
    return a if a >= b else b


@always_inline
def jfloat_to_bits(x: Float32) -> Int32:
    return bitcast[DType.int32, 1](x)


@always_inline
def jbits_to_float(b: Int32) -> Float32:
    return bitcast[DType.float32, 1](b)


@always_inline
def jdouble_to_bits(x: Float64) -> Int64:
    return bitcast[DType.int64, 1](x)


@always_inline
def jbits_to_double(b: Int64) -> Float64:
    return bitcast[DType.float64, 1](b)


@always_inline
def jfloat_isnan(x: Float32) -> Int32:
    return Int32(1) if isnan(x) else Int32(0)


@always_inline
def jfloat_isinf(x: Float32) -> Int32:
    return Int32(1) if isinf(x) else Int32(0)


@always_inline
def jdouble_isnan(x: Float64) -> Int32:
    return Int32(1) if isnan(x) else Int32(0)


@always_inline
def jdouble_isinf(x: Float64) -> Int32:
    return Int32(1) if isinf(x) else Int32(0)


@always_inline
def jint_clz(x: Int32) -> Int32:
    return Int32(count_leading_zeros(UInt32(x)))


@always_inline
def jint_ctz(x: Int32) -> Int32:
    return Int32(count_trailing_zeros(UInt32(x)))


@always_inline
def jint_bit_count(x: Int32) -> Int32:
    return Int32(pop_count(UInt32(x)))


@always_inline
def jlong_clz(x: Int64) -> Int32:
    return Int32(count_leading_zeros(UInt64(x)))


@always_inline
def jlong_bit_count(x: Int64) -> Int32:
    return Int32(pop_count(UInt64(x)))


@always_inline
def jsys_nano_time() -> Int64:
    return Int64(perf_counter_ns())


@always_inline
def jsys_current_millis() -> Int64:
    return Int64(perf_counter_ns() // 1_000_000)


@always_inline
def jruntime_cpus() -> Int32:
    var n = Int32(num_physical_cores())
    return n if n >= 1 else Int32(2)


def jthread_sleep(millis: Int64):
    if millis > 0:
        sleep(Float64(millis) / 1000.0)


@always_inline
def junsafe_alloc(bytes: Int64) -> Int64:
    var p = external_call["malloc", OptionalPointer[UInt8, MutUntrackedOrigin]](Int(bytes if bytes > 0 else 1))
    return Int64(Int(p.unsafe_value())) if p else 0


@always_inline
def junsafe_realloc(addr: Int64, bytes: Int64) -> Int64:
    var p = external_call["realloc", OptionalPointer[UInt8, MutUntrackedOrigin]](
        Pointer[UInt8, MutUntrackedOrigin](unsafe_from_address=Int(addr)),
        Int(bytes if bytes > 0 else 1),
    )
    return Int64(Int(p.unsafe_value())) if p else 0


@always_inline
def junsafe_free(addr: Int64):
    if addr != 0:
        external_call["free", NoneType](Pointer[UInt8, MutUntrackedOrigin](unsafe_from_address=Int(addr)))


@always_inline
def junsafe_memset(addr: Int64, bytes: Int64, v: Int32):
    if addr != 0 and bytes > 0:
        _ = external_call["memset", Pointer[UInt8, MutUntrackedOrigin]](
            Pointer[UInt8, MutUntrackedOrigin](unsafe_from_address=Int(addr)),
            c_int(v & 0xFF),
            Int(bytes),
        )


@always_inline
def junsafe_memcpy(src: Int64, dst: Int64, bytes: Int64):
    if src != 0 and dst != 0 and bytes > 0:
        _ = external_call["memmove", Pointer[UInt8, MutUntrackedOrigin]](
            Pointer[UInt8, MutUntrackedOrigin](unsafe_from_address=Int(dst)),
            Pointer[UInt8, MutUntrackedOrigin](unsafe_from_address=Int(src)),
            Int(bytes),
        )


@always_inline
def junsafe_get_i8(addr: Int64) -> Int32:
    return Int32(Pointer[Int8, MutUntrackedOrigin](unsafe_from_address=Int(addr))[])


@always_inline
def junsafe_put_i8(addr: Int64, v: Int32):
    Pointer[Int8, MutUntrackedOrigin](unsafe_from_address=Int(addr))[] = Int8(v)


@always_inline
def junsafe_get_i16(addr: Int64) -> Int32:
    return Int32(Pointer[Int16, MutUntrackedOrigin](unsafe_from_address=Int(addr))[])


@always_inline
def junsafe_put_i16(addr: Int64, v: Int32):
    Pointer[Int16, MutUntrackedOrigin](unsafe_from_address=Int(addr))[] = Int16(v)


@always_inline
def junsafe_get_i32(addr: Int64) -> Int32:
    return Pointer[Int32, MutUntrackedOrigin](unsafe_from_address=Int(addr))[]


@always_inline
def junsafe_put_i32(addr: Int64, v: Int32):
    Pointer[Int32, MutUntrackedOrigin](unsafe_from_address=Int(addr))[] = v


@always_inline
def junsafe_get_i64(addr: Int64) -> Int64:
    return Pointer[Int64, MutUntrackedOrigin](unsafe_from_address=Int(addr))[]


@always_inline
def junsafe_put_i64(addr: Int64, v: Int64):
    Pointer[Int64, MutUntrackedOrigin](unsafe_from_address=Int(addr))[] = v


@always_inline
def junsafe_get_f32(addr: Int64) -> Float32:
    return Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=Int(addr))[]


@always_inline
def junsafe_put_f32(addr: Int64, v: Float32):
    Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=Int(addr))[] = v


@always_inline
def junsafe_get_f64(addr: Int64) -> Float64:
    return Pointer[Float64, MutUntrackedOrigin](unsafe_from_address=Int(addr))[]


@always_inline
def junsafe_put_f64(addr: Int64, v: Float64):
    Pointer[Float64, MutUntrackedOrigin](unsafe_from_address=Int(addr))[] = v


def jint_to_string(x: Int32) raises JThrowable -> JRef:
    return new_string(String(x))


def jlong_to_string(x: Int64) raises JThrowable -> JRef:
    return new_string(String(x))


def jint_parse(s: JRef) raises JThrowable -> Int32:
    var txt = string_value(s)
    try:
        return Int32(Int(txt))
    except:
        raise JThrowable(9, "java/lang/NumberFormatException")


def jarrays_fill_i(arr: JRef, val: Int32) raises JThrowable:
    """SIMD-vectorized 8-wide Int32 array fill."""
    var hdr = arr.non_null()
    var ap = hdr.unsafe_bitcast[JArrayData[Int32]]()
    var n = Int(ap[].length)
    if n <= 0 or not ap[].data:
        return
    var ptr = ap[].data.unsafe_value()
    var vec = SIMD[DType.int32, 8](val)
    var i = 0
    while i + 8 <= n:
        ptr.unsafe_offset(i).unsafe_store(vec)
        i += 8
    while i < n:
        ptr.unsafe_offset(i)[] = val
        i += 1
