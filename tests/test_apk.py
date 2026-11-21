"""Verification test for Muffin Android Launcher APK (dual-ABI Mojo 1.2 + NDK)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class MuffinApkTest(unittest.TestCase):
    def test_build_dual_abi_signed_apk(self) -> None:
        from tools.build_apk import _find_ndk_bin, _find_sdk, build_apk

        try:
            _, _, bt_bin = _find_sdk()
            ndk_bin = _find_ndk_bin()
        except FileNotFoundError as exc:
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory() as tmp:
            apk = Path(tmp) / "muffin_test.apk"
            built = build_apk(apk)
            self.assertTrue(built.is_file())

            # 16KB page alignment & v2/v3 signature verification
            subprocess.run([str(bt_bin / "zipalign"), "-c", "-P", "16", "4", str(built)], check=True)
            ver = subprocess.check_output(
                [str(bt_bin / "apksigner"), "verify", "--verbose", str(built)], text=True
            )
            self.assertIn("Verified using v2 scheme (APK Signature Scheme v2): true", ver)
            self.assertIn("Verified using v3 scheme (APK Signature Scheme v3): true", ver)

            with zipfile.ZipFile(built) as zf:
                names = set(zf.namelist())
                self.assertIn("res/drawable/mojo_edition_banner.png", names)
                self.assertIn("res/mipmap-xxxhdpi-v4/ic_launcher.png", names)
                self.assertIn("classes.dex", names)
                self.assertIn("lib/arm64-v8a/libmuffin_core.so", names)
                self.assertIn("lib/x86_64/libmuffin_core.so", names)

                for abi in ("arm64-v8a", "x86_64"):
                    so_out = Path(tmp) / f"{abi}.so"
                    so_out.write_bytes(zf.read(f"lib/{abi}/libmuffin_core.so"))
                    syms = subprocess.check_output(
                        [str(ndk_bin / "llvm-nm"), "-D", str(so_out)], text=True
                    )
                    for req in (
                        "mc_mojo_init",
                        "mc_mojo_tick_frame",
                        "mc_mojo_shutdown",
                        "mc_mojo_terrain_height",
                        "mc_mojo_cull_chunks",
                        "Java_com_muffin_launcher_NativeBridge_nativeInitEngine",
                        "Java_com_muffin_launcher_NativeBridge_nativeProbeHardware",
                        "Java_com_muffin_launcher_NativeBridge_nativeUploadBlockAtlas",
                        "Java_com_muffin_launcher_NativeBridge_nativePollSoundEvent",
                        "mc_android_init_egl",
                        "tc_android_init_egl",
                    ):
                        self.assertIn(req, syms, f"missing {req} in {abi}")

    def test_build_lib_arm64_sharded_so(self) -> None:
        from tools.build_apk import _find_ndk_bin

        if not (shutil.which("javac") and (shutil.which("mojo") or Path("/tmp/mojoenv/bin/mojo").exists())):
            self.skipTest("javac or mojo not found")
        try:
            ndk_bin = _find_ndk_bin()
        except FileNotFoundError as exc:
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            src = t / "src" / "demo"
            cls = t / "classes"
            out = t / "out_so"
            src.mkdir(parents=True)
            cls.mkdir()
            (src / "Helper.java").write_text("package demo; public final class Helper { public static int add(int a, int b) { return a + b; } }")
            (src / "Main.java").write_text("package demo; public class Main { public static void main(String[] args) { System.out.println(Helper.add(20, 22)); } }")
            subprocess.run(["javac", "-d", str(cls), str(src / "Helper.java"), str(src / "Main.java")], check=True)

            subprocess.run(
                [
                    "python3", "-m", "tools.muffin", "build-lib",
                    "-i", str(cls), "-o", str(out),
                    "--entry", "demo.Main", "--shards", "2", "--jobs", "2",
                    "--target-triple", "aarch64-unknown-linux-android26",
                    "--name", "libtest_mc.so",
                ],
                cwd=REPO_ROOT,
                check=True,
            )
            so_file = out / "libtest_mc.so"
            self.assertTrue(so_file.is_file())
            hdr = subprocess.check_output([str(ndk_bin / "llvm-readelf"), "-h", str(so_file)], text=True)
            self.assertIn("AArch64", hdr)
            syms = subprocess.check_output([str(ndk_bin / "llvm-nm"), "-D", str(so_file)], text=True)
            self.assertIn("mc_android_entry", syms)
            self.assertIn("j2z_android_entry", syms)


if __name__ == "__main__":
    unittest.main()
