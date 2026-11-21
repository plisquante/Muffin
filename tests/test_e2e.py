"""
End-to-end tests for Muffin's Java bytecode -> Mojo 1.2 translator.

1. Compiles a Java fixture (OOP inheritance, polymorphism, devirtualization,
   arrays, loops, static fields, instanceof, checkcast, try/catch) with `javac`,
   translates it to Mojo 1.2 with `tools.muffin`, executes both the JVM and the
   compiled Mojo binary, and asserts identical output.
2. Smoke-tests lifting + Mojo emission on real Minecraft 26.3 classes from
   `work/versions/26.3/client.jar` (when present).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

JAVA_FIXTURE = r"""
package demo;

abstract class Shape {
    int id;
    Shape(int id) { this.id = id; }
    abstract int area();
    int taggedArea() { return this.id * 100 + this.area(); }
}

final class Rect extends Shape {
    int w;
    int h;
    Rect(int id, int w, int h) {
        super(id);
        this.w = w;
        this.h = h;
    }
    int area() { return this.w * this.h; }
}

final class Square extends Shape {
    int side;
    Square(int id, int side) {
        super(id);
        this.side = side;
    }
    int area() { return this.side * this.side; }
}

public class Main {
    static int counter = 5;

    static int sumArray(int[] arr) {
        int acc = 0;
        for (int i = 0; i < arr.length; i++) {
            acc += arr[i];
        }
        return acc;
    }

    static int safeDiv(int a, int b) {
        try {
            return a / b;
        } catch (ArithmeticException e) {
            return -999;
        }
    }

    public static void main(String[] args) {
        Shape a = new Rect(1, 6, 7);
        Shape b = new Square(2, 5);
        System.out.println(a.taggedArea());
        System.out.println(b.taggedArea());
        System.out.println(a instanceof Rect);
        System.out.println(b instanceof Rect);
        Rect r = (Rect) a;
        System.out.println(r.w + r.h);

        int[] nums = new int[5];
        for (int i = 0; i < nums.length; i++) {
            nums[i] = (i + 1) * counter;
        }
        System.out.println(sumArray(nums));
        System.out.println(safeDiv(42, 6));
        System.out.println(safeDiv(42, 0));
        System.out.println("Muffin Java->Mojo OK");
    }
}
"""


def _find_mojo() -> str | None:
    for cand in (shutil.which("mojo"), "/tmp/mojoenv/bin/mojo"):
        if cand and Path(cand).exists():
            return str(cand)
    return None


class MuffinE2ETest(unittest.TestCase):
    def test_java_to_mojo_execution(self) -> None:
        javac = shutil.which("javac")
        java = shutil.which("java")
        mojo = _find_mojo()
        self.assertIsNotNone(javac, "javac is required")
        self.assertIsNotNone(java, "java is required")
        self.assertIsNotNone(mojo, "mojo is required")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            src_dir = tmp / "src" / "demo"
            cls_dir = tmp / "classes"
            src_dir.mkdir(parents=True)
            cls_dir.mkdir()
            (src_dir / "Main.java").write_text(JAVA_FIXTURE)

            subprocess.run([javac, "-d", str(cls_dir), str(src_dir / "Main.java")], check=True)
            java_out = subprocess.check_output([java, "-cp", str(cls_dir), "demo.Main"], text=True)

            mojo_file = tmp / "out.mojo"
            subprocess.run(
                ["python3", "-m", "tools.muffin", "translate", "-i", str(cls_dir), "-o", str(mojo_file), "--entry", "demo.Main"],
                cwd=REPO_ROOT,
                check=True,
            )
            mojo_out = subprocess.check_output(
                [mojo, "run", "-I", str(REPO_ROOT), str(mojo_file)],
                text=True,
            )
            self.assertEqual(mojo_out, java_out)

    def test_minecraft_subset_codegen(self) -> None:
        jar = REPO_ROOT / "work" / "versions" / "26.3" / "client.jar"
        if not jar.exists():
            self.skipTest("client.jar not downloaded")
        with tempfile.TemporaryDirectory() as tmpdir:
            out_mojo = Path(tmpdir) / "mc_subset.mojo"
            subprocess.run(
                [
                    "python3", "-m", "tools.muffin", "translate",
                    "-i", str(jar),
                    "-o", str(out_mojo),
                    "--filter", "net/minecraft/core/Vec3i",
                    "--filter", "net/minecraft/core/BlockPos",
                    "--filter", "net/minecraft/util/Mth",
                ],
                cwd=REPO_ROOT,
                check=True,
            )
            txt = out_mojo.read_text()
            self.assertIn("struct C_net_minecraft_core_Vec3iData:", txt)
            self.assertIn("struct C_net_minecraft_core_BlockPosData:", txt)
            self.assertIn("struct C_net_minecraft_util_MthData:", txt)


if __name__ == "__main__":
    unittest.main()
