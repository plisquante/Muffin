"""
Muffin Vanilla Hot-Kernel Patch System.

Allows replacing selected JVM methods (such as `Mth.sin`, `Mth.cos`,
`SimpleBitStorage`, or chunk section loops) with hand-tuned SIMD / Multi-Core
Mojo kernels from `patches/` at codegen time — keeping zero Mojang code in
the repository.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PatchSpec:
    owner: str
    name: str
    descriptor: str
    mojo_expr: str  # Expression evaluated in terms of (_s, arg0, p0, p1, ...)


# Built-in verified SIMD/intrinsic replacements for Minecraft 26.3 hot paths
BUILTIN_PATCHES: dict[tuple[str, str, str], PatchSpec] = {
    (
        "net/minecraft/util/Mth",
        "sin",
        "(D)F",
    ): PatchSpec(
        "net/minecraft/util/Mth",
        "sin",
        "(D)F",
        "Float32(jmath_sin(p0))",
    ),
    (
        "net/minecraft/util/Mth",
        "sin",
        "(F)F",
    ): PatchSpec(
        "net/minecraft/util/Mth",
        "sin",
        "(F)F",
        "Float32(jmath_sin(Float64(p0)))",
    ),
    (
        "net/minecraft/util/Mth",
        "cos",
        "(D)F",
    ): PatchSpec(
        "net/minecraft/util/Mth",
        "cos",
        "(D)F",
        "Float32(jmath_cos(p0))",
    ),
    (
        "net/minecraft/util/Mth",
        "cos",
        "(F)F",
    ): PatchSpec(
        "net/minecraft/util/Mth",
        "cos",
        "(F)F",
        "Float32(jmath_cos(Float64(p0)))",
    ),
    (
        "net/minecraft/util/Mth",
        "sqrt",
        "(F)F",
    ): PatchSpec(
        "net/minecraft/util/Mth",
        "sqrt",
        "(F)F",
        "Float32(jmath_sqrt(Float64(p0)))",
    ),
    (
        "net/minecraft/util/Mth",
        "floor",
        "(F)I",
    ): PatchSpec(
        "net/minecraft/util/Mth",
        "floor",
        "(F)I",
        "Int32(jmath_floor(Float64(p0)))",
    ),
    (
        "net/minecraft/util/Mth",
        "floor",
        "(D)I",
    ): PatchSpec(
        "net/minecraft/util/Mth",
        "floor",
        "(D)I",
        "Int32(jmath_floor(p0))",
    ),
    (
        "net/minecraft/util/Mth",
        "clamp",
        "(III)I",
    ): PatchSpec(
        "net/minecraft/util/Mth",
        "clamp",
        "(III)I",
        "p1 if p0 < p1 else (p2 if p0 > p2 else p0)",
    ),
    (
        "net/minecraft/util/Mth",
        "clamp",
        "(JJJ)J",
    ): PatchSpec(
        "net/minecraft/util/Mth",
        "clamp",
        "(JJJ)J",
        "p1 if p0 < p1 else (p2 if p0 > p2 else p0)",
    ),
    (
        "net/minecraft/util/Mth",
        "clamp",
        "(FFF)F",
    ): PatchSpec(
        "net/minecraft/util/Mth",
        "clamp",
        "(FFF)F",
        "p1 if p0 < p1 else (p2 if p0 > p2 else p0)",
    ),
    (
        "net/minecraft/util/Mth",
        "clamp",
        "(DDD)D",
    ): PatchSpec(
        "net/minecraft/util/Mth",
        "clamp",
        "(DDD)D",
        "p1 if p0 < p1 else (p2 if p0 > p2 else p0)",
    ),
}
