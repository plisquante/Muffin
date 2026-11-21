"""
Unit & stress tests for Muffin's Mojo 1.2 Zero-GC Runtime (`runtime/muffin/`):
- FrameArena mark/alloc/restore (zero malloc/free inside scopes)
- Persistent multi-core WorkerPool (`pthread_cond` + atomic chunking)
- JVMS exact arithmetic (`idiv_j`, `irem_j`, `ldiv_j`, `lrem_j`, `f2i_j`, `d2l_j`, `fcmp_j`)
- Compact Strings, StringBuilder, multi-dim arrays, SIMD `jarrays_fill_i`
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

RUNTIME_TEST_MOJO = r"""
from std.atomic import Atomic
from std.memory import MutOpaquePointer, Pointer
from std.origin import MutUntrackedOrigin
from std.sys import size_of
from runtime.muffin import (
    AudioEngine,
    FrameArena,
    Frustum,
    GpuInstance,
    JRef,
    Mat4f,
    ObjHeader,
    RhiContext,
    SectionSnapshot,
    Vec4f,
    WorkerPool,
    ZlibCodec,
    alloc_raw,
    array_get,
    array_len,
    array_set,
    arraycopy,
    batch_gpu_instances,
    cull_sections_parallel,
    d2i_j,
    d2l_j,
    fcmp_j,
    free_raw,
    idiv_j,
    irem_j,
    jarrays_fill_i,
    jint_bit_count,
    jmath_sqrt,
    ldiv_j,
    lrem_j,
    new_array,
    new_array_arena,
    new_multi_array,
    new_sb,
    new_string,
    sb_append_bool,
    sb_append_char,
    sb_append_str,
    sb_append_val,
    sb_to_string,
    string_equals,
    string_hash,
    string_length,
    string_substring,
    string_value,
    translate_glsl_version,
    unpack_bit_storage,
)

def chunk_worker(start: Int, end: Int, raw: MutOpaquePointer[MutUntrackedOrigin]):
    var acc = raw.unsafe_bitcast[Atomic[Int64]]()
    var local: Int64 = 0
    for i in range(start, end):
        local += Int64(i)
    _ = acc[].fetch_add(local)

def main() raises:
    # 0. Verify 16-byte compact ObjHeader
    if size_of[ObjHeader]() != 16:
        raise Error("ObjHeader must be 16 bytes")

    # 1. FrameArena mark/restore churn
    var arena = FrameArena()
    for _ in range(100):
        var mk = arena.mark()
        var arr = new_array_arena[Int32](arena, 8, 64)
        jarrays_fill_i(arr, 17)
        if array_get[Int32](arr, 63) != 17:
            raise Error("arena fill mismatch")
        arena.restore(mk)

    # 2. Persistent WorkerPool across 100 batches
    var pool = WorkerPool()
    var acc = Atomic[Int64](0)
    for _ in range(100):
        pool.parallel_for(500, chunk_worker, Pointer(to=acc).unsafe_bitcast[NoneType](), grain=32)
    if acc.load() != 100 * 124750:
        raise Error("worker pool mismatch")

    # 3. JVMS arithmetic edge cases
    if idiv_j(-7, 2) != -3 or irem_j(-7, 2) != -1:
        raise Error("trunc div mismatch")
    if idiv_j(-2147483648, -1) != -2147483648 or irem_j(-2147483648, -1) != 0:
        raise Error("min_int div mismatch")
    if ldiv_j(-9223372036854775808, -1) != -9223372036854775808 or lrem_j(-9, 4) != -1:
        raise Error("long div mismatch")
    if d2i_j(1e20) != 2147483647 or d2i_j(-1e20) != -2147483648:
        raise Error("d2i saturation mismatch")
    if d2l_j(1e30) != 9223372036854775807:
        raise Error("d2l saturation mismatch")
    var nan_f = Float32(0.0) / Float32(0.0)
    if fcmp_j(nan_f, 1.0, -1) != -1 or fcmp_j(nan_f, 1.0, 1) != 1:
        raise Error("nan fcmp mismatch")

    # 4. Strings, StringBuilder & multi-dim arrays
    var sb = new_sb()
    _ = sb_append_str(sb, new_string("mc:"))
    _ = sb_append_val(sb, Int32(26))
    _ = sb_append_char(sb, 46)
    _ = sb_append_val(sb, Int32(3))
    _ = sb_append_bool(sb, 1)
    var s = sb_to_string(sb)
    if string_value(s) != "mc:26.3true":
        raise Error("sb mismatch")
    if string_length(s) != 11:
        raise Error("strlen mismatch")
    if string_value(string_substring(s, 3, 7)) != "26.3":
        raise Error("substr mismatch")
    if string_equals(s, new_string("mc:26.3true")) != 1:
        raise Error("streq mismatch")
    _ = string_hash(s)

    var dims = List[Int32]()
    dims.append(2)
    dims.append(3)
    var grid = new_multi_array[Int32](dims, 0, 9)
    var row1 = array_get[JRef](grid, 1)
    array_set[Int32](row1, 2, 99)
    if array_get[Int32](array_get[JRef](grid, 1), 2) != 99:
        raise Error("multi array mismatch")

    # 5. Zlib (libz.so.1) compress + uncompress + CRC32
    var zlib = ZlibCodec()
    var raw_in = List[UInt8](length=16, fill=65)
    var comp_buf = List[UInt8](length=64, fill=0)
    var decomp_buf = List[UInt8](length=16, fill=0)
    var in_p = raw_in.unsafe_ptr().unsafe_origin_cast[MutUntrackedOrigin]()
    var cmp_p = comp_buf.unsafe_ptr().unsafe_origin_cast[MutUntrackedOrigin]()
    var dec_p = decomp_buf.unsafe_ptr().unsafe_origin_cast[MutUntrackedOrigin]()
    var clen = UInt64(64)
    if zlib.compress(cmp_p, clen, in_p, 16) != 0:
        raise Error("zlib compress failed")
    var dlen = UInt64(16)
    if zlib.uncompress(dec_p, dlen, cmp_p, Int(clen)) != 0 or dlen != 16:
        raise Error("zlib uncompress failed")
    if zlib.crc32(in_p, 16) != zlib.crc32(dec_p, 16):
        raise Error("crc32 mismatch")

    # 6. Zero-LWJGL RHI + Blaze3D GLSL shader normalizer + OpenAL probe
    var rhi = RhiContext(1280, 720, True)
    rhi.begin_frame()
    rhi.record_instanced_draw(128, 36)
    var glsl = translate_glsl_version("#version 150\nout vec4 fragColor;\nvoid main(){}", True)
    if not glsl.startswith("#version 300 es"):
        raise Error("glsl translation mismatch")
    var audio = AudioEngine()
    _ = audio.available

    # 7. Multi-core World Culling + GPU Instancing + SimpleBitStorage unpack
    var secs = List[SectionSnapshot](capacity=256)
    for i in range(256):
        var snap = SectionSnapshot()
        snap.min_v = Vec4f(Float32((i % 16) * 16 - 128), 0.0, Float32((i // 16) * 16 - 128), 0.0)
        snap.max_v = snap.min_v + Vec4f(16.0, 16.0, 16.0, 0.0)
        snap.non_empty = (i % 2 == 0)
        secs.append(snap^)
    var vis = cull_sections_parallel(pool, secs.unsafe_ptr().unsafe_origin_cast[MutUntrackedOrigin](), 256, Frustum(), Vec4f(0, 64, 0, 0))
    if vis != 128:
        raise Error("world cull count mismatch")

    var packed = List[UInt64]()
    packed.append(0x00000000_00000041)
    packed.append(0)
    var ids = List[Int32](length=32, fill=0)
    unpack_bit_storage(packed.unsafe_ptr(), ids.unsafe_ptr(), 4, 4)
    _ = len(packed)
    if ids[0] != 1 or ids[1] != 4:
        raise Error("bitstorage unpack mismatch")

    print("RUNTIME_ALL_OK")
"""


def _find_mojo() -> str | None:
    for c in (shutil.which("mojo"), "/tmp/mojoenv/bin/mojo"):
        if c and Path(c).exists():
            return str(c)
    return None


class MuffinRuntimeTest(unittest.TestCase):
    def test_runtime_core_arena_pool_and_jdk(self) -> None:
        mojo = _find_mojo()
        self.assertIsNotNone(mojo)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rt_suite.mojo"
            p.write_text(RUNTIME_TEST_MOJO)
            out = subprocess.check_output([mojo, "run", "-I", str(REPO_ROOT), str(p)], text=True)
            self.assertEqual(out.strip(), "RUNTIME_ALL_OK")


if __name__ == "__main__":
    unittest.main()
