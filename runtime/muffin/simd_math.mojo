"""
Muffin SIMD Math & Minecraft BitStorage Intrinsics (Mojo 1.2).

Replaces scalar Java `org.joml` / `Vec3` / `Mth` / `SimpleBitStorage` hot paths
with hardware-vectorized `SIMD[DType.float32, 4]` and batch bit-unpacking.
"""

from std.math import cos, sin, sqrt
from std.memory import MutPointer, Pointer
from std.origin import MutUntrackedOrigin

comptime Vec4f = SIMD[DType.float32, 4]


@fieldwise_init
struct Mat4f(Copyable, Defaultable):
    """Column-major 4x4 float32 matrix backed by 4 SIMD columns."""

    var c0: Vec4f
    var c1: Vec4f
    var c2: Vec4f
    var c3: Vec4f

    def __init__(out self):
        self.c0 = Vec4f(1, 0, 0, 0)
        self.c1 = Vec4f(0, 1, 0, 0)
        self.c2 = Vec4f(0, 0, 1, 0)
        self.c3 = Vec4f(0, 0, 0, 1)

    @always_inline
    def mul_vec(self, v: Vec4f) -> Vec4f:
        return self.c0 * v[0] + self.c1 * v[1] + self.c2 * v[2] + self.c3 * v[3]

    @always_inline
    def mul(self, rhs: Mat4f) -> Mat4f:
        return Mat4f(
            self.mul_vec(rhs.c0),
            self.mul_vec(rhs.c1),
            self.mul_vec(rhs.c2),
            self.mul_vec(rhs.c3),
        )


@always_inline
def simd_dot3(a: Vec4f, b: Vec4f) -> Float32:
    var p = a * b
    return p[0] + p[1] + p[2]


@always_inline
def simd_normalize3(v: Vec4f) -> Vec4f:
    var len_sq = simd_dot3(v, v)
    if len_sq <= 1e-12:
        return Vec4f(0, 0, 0, 0)
    return v * (Float32(1.0) / sqrt(len_sq))


@always_inline
def simd_cross3(a: Vec4f, b: Vec4f) -> Vec4f:
    return Vec4f(
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
        0.0,
    )


def unpack_bit_storage(
    packed: Pointer[UInt64, _],
    out_ids: MutPointer[Int32, _],
    count: Int,
    bits_per_entry: Int,
):
    """High-speed Mojang SimpleBitStorage (non-spanning 64-bit word packing) unpacker."""
    if bits_per_entry <= 0:
        for i in range(count):
            out_ids.unsafe_offset(i)[] = 0
        return
    var per_long = 64 // bits_per_entry
    var mask = (UInt64(1) << UInt64(bits_per_entry)) - UInt64(1)
    var idx = 0
    var word_i = 0
    while idx < count:
        var w = packed.unsafe_offset(word_i)[]
        var n = per_long if idx + per_long <= count else (count - idx)
        for j in range(n):
            out_ids.unsafe_offset(idx + j)[] = Int32(w & mask)
            w >>= UInt64(bits_per_entry)
        idx += n
        word_i += 1
