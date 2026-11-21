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
            acc += oneAlloc(i);
        }
        return acc;
    }

    static int oneAlloc(int i) {
        Pair p = new Pair(i, i + 7);
        return p.a * 3 + p.b;
    }

    public static void main(String[] args) {
        long t0 = System.nanoTime();
        long f = 0;
        for (int i = 0; i < 200000; i++) {
            f += fib(40);
        }
        long t1 = System.nanoTime();
        int c = callLoop(new Step(7), 20000000);
        long t2 = System.nanoTime();
        int e = eaLoop(2000000);
        long t3 = System.nanoTime();
        System.out.println("check=" + f + "," + c + "," + e);
        System.out.println("fib_ms=" + ((t1 - t0) / 1000000L));
        System.out.println("call_ms=" + ((t2 - t1) / 1000000L));
        System.out.println("ea_ms=" + ((t3 - t2) / 1000000L));
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
from std.memory import MutOpaquePointer, Pointer
from std.origin import MutUntrackedOrigin
from std.time import perf_counter_ns
from runtime.muffin import WorkerPool, cpu_count, jarrays_fill_i

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

def main():
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
