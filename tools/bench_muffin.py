"""
Muffin vs HotSpot JVM Microbenchmark Harness.

Measures:
1. Recursive / loop compute (`fib`, tight integer kernel)
2. Polymorphic & devirtualized call loop (CHA devirt vs JVM)
3. Escape-Analysis FrameArena allocation churn (zero-malloc vs JVM GC)
4. Multi-core parallel chunk/grid kernel (`WorkerPool` in Mojo vs Java single-core)
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BENCH_JAVA = r"""
package bench;

interface Op {
    int apply(int x);
}

final class Step implements Op {
    final int bias;
    Step(int bias) { this.bias = bias; }
    public int apply(int x) { return (x * 3 + bias) ^ (x >>> 2); }
}

final class Pair {
    int a;
    int b;
    Pair(int a, int b) { this.a = a; this.b = b; }
}

final class Vec3d {
    final double x, y, z;
    Vec3d(double x, double y, double z) { this.x = x; this.y = y; this.z = z; }
}

final class Box3d {
    final double minX, minY, minZ, maxX, maxY, maxZ;
    Box3d(double minX, double minY, double minZ, double maxX, double maxY, double maxZ) {
        this.minX = minX; this.minY = minY; this.minZ = minZ;
        this.maxX = maxX; this.maxY = maxY; this.maxZ = maxZ;
    }
    boolean containsVec(Vec3d v) {
        return v.x >= minX && v.x < maxX && v.y >= minY && v.y < maxY && v.z >= minZ && v.z < maxZ;
    }
    boolean overlaps(Box3d o) {
        return minX < o.maxX && maxX > o.minX && minY < o.maxY && maxY > o.minY && minZ < o.maxZ && maxZ > o.minZ;
    }
}

public class Main {
    static long fib(int n) {
        if (n <= 1) return n;
        long a = 0, b = 1;
        for (int i = 2; i <= n; i++) {
            long c = a + b;
            a = b;
            b = c;
        }
        return b;
    }

    static int callLoop(Op op, int iters) {
        int acc = 1;
        for (int i = 0; i < iters; i++) {
            acc = op.apply(acc + i);
        }
        return acc;
    }

    static int eaLoop(int n) {
        int acc = 0;
        for (int i = 0; i < n; i++) {
            Pair p = new Pair(i, i + 7);
            acc += p.a * 3 + p.b;
        }
        return acc;
    }

    static double fade(double t) { return t * t * t * (t * (t * 6.0 - 15.0) + 10.0); }
    static double lerp(double d, double a, double b) { return a + d * (b - a); }

    static double grad(int hash, double x, double y, double z) {
        int h = hash & 15;
        double u = h < 8 ? x : y;
        double v = h < 4 ? y : (h == 12 || h == 14 ? x : z);
        return ((h & 1) == 0 ? u : -u) + ((h & 2) == 0 ? v : -v);
    }

    static long terrainNoiseBench(int cols) {
        int[] p = new int[512];
        for (int i = 0; i < 256; i++) {
            int val = (i * 1103515245 + 12345) & 255;
            p[i] = val;
            p[i + 256] = val;
        }
        long sum = 0;
        for (int i = 0; i < cols; i++) {
            double x = (i & 255) * 0.03125;
            double y = ((i >>> 8) & 63) * 0.0625;
            double z = (i >>> 14) * 0.03125;
            int xi = (int) Math.floor(x);
            int yi = (int) Math.floor(y);
            int zi = (int) Math.floor(z);
            int X = xi & 255, Y = yi & 255, Z = zi & 255;
            double fx = x - xi, fy = y - yi, fz = z - zi;
            double u = fade(fx), v = fade(fy), w = fade(fz);
            int A = p[X] + Y, AA = p[A] + Z, AB = p[A + 1] + Z;
            int B = p[X + 1] + Y, BA = p[B] + Z, BB = p[B + 1] + Z;
            double res = lerp(w,
                lerp(v, lerp(u, grad(p[AA], fx, fy, fz), grad(p[BA], fx - 1, fy, fz)),
                        lerp(u, grad(p[AB], fx, fy - 1, fz), grad(p[BB], fx - 1, fy - 1, fz))),
                lerp(v, lerp(u, grad(p[AA + 1], fx, fy, fz - 1), grad(p[BA + 1], fx - 1, fy, fz - 1)),
                        lerp(u, grad(p[AB + 1], fx, fy - 1, fz - 1), grad(p[BB + 1], fx - 1, fy - 1, fz - 1))));
            sum += (long) (res * 1024.0);
        }
        return sum;
    }

    static int aabbCollisionBench(int iters) {
        Box3d player = new Box3d(0.0, 64.0, 0.0, 0.6, 65.8, 0.6);
        int hits = 0;
        for (int i = 0; i < iters; i++) {
            double dx = ((i & 31) - 16) * 0.1;
            double dy = 64.0 + ((i >>> 5) & 7) * 0.3;
            double dz = (((i >>> 8) & 31) - 16) * 0.1;
            Box3d block = new Box3d(dx, dy, dz, dx + 1.0, dy + 1.0, dz + 1.0);
            Vec3d pt = new Vec3d(dx + 0.5, dy + 0.5, dz + 0.5);
            if (player.overlaps(block) || player.containsVec(pt)) {
                hits++;
            }
        }
        return hits;
    }

    static long bitStorageBench(int ops) {
        long[] data = new long[512]; // 5-bit packed voxel palette (12 entries/long = 6144 blocks)
        long acc = 0;
        for (int i = 0; i < ops; i++) {
            int idx = i & 4095;
            int cell = idx / 12;
            int shift = (idx % 12) * 5;
            long val = (long) ((i * 7 + 3) & 31);
            data[cell] = (data[cell] & ~(31L << shift)) | (val << shift);
            acc += (data[cell] >>> shift) & 31L;
        }
        return acc;
    }

    public static void main(String[] args) {
        long t0 = System.nanoTime();
        long f = 0;
        for (int i = 0; i < 200000; i++) f += fib(40);
        long t1 = System.nanoTime();
        int c = callLoop(new Step(7), 20000000);
        long t2 = System.nanoTime();
        int e = eaLoop(2000000);
        long t3 = System.nanoTime();
        long n = terrainNoiseBench(300000);
        long t4 = System.nanoTime();
        int h = aabbCollisionBench(2000000);
        long t5 = System.nanoTime();
        long b = bitStorageBench(4000000);
        long t6 = System.nanoTime();
        System.out.println("check=" + f + "," + c + "," + e + "," + n + "," + h + "," + b);
        System.out.println("fib_ms=" + ((t1 - t0) / 1000000L));
        System.out.println("call_ms=" + ((t2 - t1) / 1000000L));
        System.out.println("ea_ms=" + ((t3 - t2) / 1000000L));
        System.out.println("noise_ms=" + ((t4 - t3) / 1000000L));
        System.out.println("aabb_ms=" + ((t5 - t4) / 1000000L));
        System.out.println("bits_ms=" + ((t6 - t5) / 1000000L));
    }
}
"""


def _find_mojo() -> str:
    for c in (shutil.which("mojo"), "/tmp/mojoenv/bin/mojo"):
        if c and Path(c).exists():
            return str(c)
    raise SystemExit("mojo binary not found")


MULTICORE_CHUNK_MOJO = r"""
from std.atomic import Atomic
from std.collections import List
from std.memory import MutOpaquePointer, Pointer
from std.origin import MutUntrackedOrigin
from std.time import perf_counter_ns
from runtime.muffin import (
    WorkerPool, cpu_count, Frustum, SectionSnapshot, Vec4f, cull_sections_parallel,
    probe_rhi_caps, ZlibCodec,
)

struct ChunkSimCtx:
    var sections: Int
    var checksum: Atomic[Int64]
    def __init__(out self, sections: Int):
        self.sections = sections
        self.checksum = Atomic[Int64](0)

def sim_section_range(start: Int, end: Int, raw: MutOpaquePointer[MutUntrackedOrigin]):
    var ctx = raw.unsafe_bitcast[ChunkSimCtx]()
    var local: Int64 = 0
    for s in range(start, end):
        # Simulate 4096-block section (16x16x16) light/state pass with 8-wide SIMD
        var v = SIMD[DType.int32, 8](Int32(s), Int32(s + 1), Int32(s + 2), Int32(s + 3), Int32(s + 4), Int32(s + 5), Int32(s + 6), Int32(s + 7))
        var acc = SIMD[DType.int32, 8](0)
        for _ in range(512):  # 512 * 8 = 4096 blocks per section
            acc = (acc ^ (v * 3 + 7)) & 0xFFFF
            v += 1
        local += Int64(acc.reduce_add())
    _ = ctx[].checksum.fetch_add(local)

def main() raises:
    var sections = 2048
    var ctx1 = ChunkSimCtx(sections)
    var t0 = perf_counter_ns()
    sim_section_range(0, sections, Pointer(to=ctx1).unsafe_bitcast[NoneType]().unsafe_origin_cast[MutUntrackedOrigin]())
    var t1 = perf_counter_ns()

    var pool = WorkerPool()
    var ctxN = ChunkSimCtx(sections)
    var t2 = perf_counter_ns()
    pool.parallel_for(sections, sim_section_range, Pointer(to=ctxN).unsafe_bitcast[NoneType](), grain=32)
    var t3 = perf_counter_ns()

    print("cpus=", cpu_count(), "single_ms=", (t1 - t0) // 1_000_000, "multicore_ms=", (t3 - t2) // 1_000_000, "check_ok=", ctx1.checksum.load() == ctxN.checksum.load())

    # 3-stage parallel frustum/section cull benchmark (16,384 sections)
    var total_secs = 16384
    var snaps = List[SectionSnapshot](capacity=total_secs)
    for i in range(total_secs):
        var cx = Float32((i % 64) * 16 - 512)
        var cz = Float32(((i // 64) % 64) * 16 - 512)
        var cy = Float32((i // 4096) * 16)
        snaps.append(SectionSnapshot(Vec4f(cx, cy, cz, 0.0), Vec4f(cx + 16.0, cy + 16.0, cz + 16.0, 0.0), True, UInt64(0xFFFFFFFFF), UInt8(0), False))
    var fr = Frustum()
    var t4 = Int64(perf_counter_ns())
    var vis_count = 0
    for _ in range(100):
        vis_count = cull_sections_parallel(
            pool,
            Pointer[SectionSnapshot, _](to=snaps[0]).unsafe_origin_cast[MutUntrackedOrigin](),
            total_secs,
            fr,
            Vec4f(0.0, 64.0, 0.0, 0.0),
        )
    var t5 = Int64(perf_counter_ns())
    var cull_us = (t5 - t4) // 100_000
    if cull_us < 0:
        cull_us = 0
    _ = len(snaps)
    var rhi = probe_rhi_caps()
    var crc_buf = List[UInt8](capacity=64)
    for k in range(64):
        crc_buf.append(UInt8(k))
    var z_ok = False
    try:
        var zc = ZlibCodec()
        z_ok = zc.crc32(Pointer[UInt8, _](to=crc_buf[0]).unsafe_origin_cast[MutUntrackedOrigin](), 64) > 0
    except:
        z_ok = False
    _ = len(crc_buf)
    print("cull_sections=", total_secs, "visible=", vis_count, "cull_us_per_pass=", cull_us, "vulkan=", rhi.has_vulkan_lib, "gl=", rhi.has_gl_lib, "zlib_ok=", z_ok)
"""


def main() -> None:
    javac = shutil.which("javac") or "javac"
    java = shutil.which("java") or "java"
    mojo = _find_mojo()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        src = tmp / "src" / "bench"
        cls = tmp / "classes"
        src.mkdir(parents=True)
        cls.mkdir()
        (src / "Main.java").write_text(BENCH_JAVA)
        subprocess.run([javac, "-d", str(cls), str(src / "Main.java")], check=True)

        mojo_src = tmp / "bench.mojo"
        native_bin = tmp / "bench_bin"
        subprocess.run(
            ["python3", "-m", "tools.muffin", "translate", "-i", str(cls), "-o", str(mojo_src), "--entry", "bench.Main"],
            cwd=REPO_ROOT,
            check=True,
        )
        t_compile = time.perf_counter()
        subprocess.run([mojo, "build", "-I", str(REPO_ROOT), str(mojo_src), "-o", str(native_bin)], check=True)
        print(f"[muffin] mojo build took {time.perf_counter() - t_compile:.2f}s")

        print("=== JVM (OpenJDK 21 HotSpot) ===")
        t_j = time.perf_counter()
        j_out = subprocess.check_output([java, "-cp", str(cls), "bench.Main"], text=True)
        print(j_out.strip(), f"| wall={time.perf_counter() - t_j:.3f}s")

        print("=== Muffin Native (Mojo 1.2 Zero-GC) ===")
        t_m = time.perf_counter()
        m_out = subprocess.check_output([str(native_bin)], text=True)
        print(m_out.strip(), f"| wall={time.perf_counter() - t_m:.3f}s")

        mc_src = tmp / "chunk_mc.mojo"
        mc_bin = tmp / "chunk_mc_bin"
        mc_src.write_text(MULTICORE_CHUNK_MOJO)
        subprocess.run([mojo, "build", "-I", str(REPO_ROOT), str(mc_src), "-o", str(mc_bin)], check=True)
        print("=== Muffin Multi-Core + SIMD Chunk Tick (2048 x 4096 blocks) ===")
        print(subprocess.check_output([str(mc_bin)], text=True).strip())


if __name__ == "__main__":
    main()
