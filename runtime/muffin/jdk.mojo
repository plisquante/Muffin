"""
Muffin JDK Fast-Path Intrinsics (Mojo 1.2).

Direct native / SIMD / zero-GC implementations for the hottest JDK methods
found in `client.jar` (`java.lang.Math`, `Integer`, `Long`, `Arrays`, `System`).
"""

from std.bit import bit_reverse, byte_swap, count_leading_zeros, count_trailing_zeros, pop_count
from std.math import atan2, ceil, cos, floor, sin, sqrt
from std.memory import Pointer
from std.origin import MutUntrackedOrigin
from std.time import perf_counter_ns

from .core import JArrayData, JRef, JThrowable, new_string, string_value


@always_inline
def jmath_sin(x: Float64) -> Float64:
    return sin(x)


@always_inline
def jmath_cos(x: Float64) -> Float64:
    return cos(x)


@always_inline
def jmath_sqrt(x: Float64) -> Float64:
    return sqrt(x)


@always_inline
def jmath_floor(x: Float64) -> Float64:
    return floor(x)


@always_inline
def jmath_ceil(x: Float64) -> Float64:
    return ceil(x)


@always_inline
def jmath_atan2(y: Float64, x: Float64) -> Float64:
    return atan2(y, x)


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


def jint_to_string(x: Int32) raises JThrowable -> JRef:
    return new_string(String(x))


def jlong_to_string(x: Int64) raises JThrowable -> JRef:
    return new_string(String(x))


def jint_parse(s: JRef) raises JThrowable -> Int32:
    var txt = string_value(s)
    try:
        return Int32(Int(txt))
    except:
        raise JThrowable(10, "java/lang/NumberFormatException")


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
