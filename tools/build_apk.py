#!/usr/bin/env python3
"""
Reproducible Dual-ABI (arm64-v8a + x86_64) APK Builder for Muffin: Minecraft Mojo Edition.

Compiles `android/muffin/jni/muffin_core.mojo` (Mojo 1.2 native C-ABI core) +
`android/muffin/jni/muffin_android_jni.c` with Android NDK into `libmuffin_core.so`
(16KB page-aligned for Android 15+), packages resources + brand logo, compiles Java,
dexes with D8, zipaligns, and signs the APK with apksigner.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import urllib.request
import zipfile
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ANDROID_DIR = REPO_ROOT / "android" / "muffin"
BRAND_BANNER = REPO_ROOT / "brand" / "mojo_edition_banner.png"
DEFAULT_OUT_APK = REPO_ROOT / "releases" / "Muffin-Minecraft-Mojo-Edition.apk"

CACHE_ROOT = Path.home() / ".cache" / "muffin"
KEYSTORE_PATH = CACHE_ROOT / "muffin-release.jks"
KEY_ALIAS = "muffin_mojo"
KEY_PASS = "muffin_mojo_2026"

SHIZUKU_VERSION = "13.1.5"
SHIZUKU_AARS = {
    "api": "1f760c56493bdbc01462930e75bb325033b36fe2",
    "provider": "4135b99cae9362fe2f63380230d04a9bcba9be3f",
    "shared": "8c4e73ce466cd27aff10565d2ec49809c258f974",
    "aidl": "457504c277d796adea698b80b4e765f98dd0799a",
}

ABIS = [
    ("arm64-v8a", "aarch64-unknown-linux-android26", "aarch64-linux-android26-clang"),
    ("x86_64", "x86_64-unknown-linux-android26", "x86_64-linux-android26-clang"),
]


def _run(desc: str, cmd: list[str]) -> str:
    print(f"[muffin-apk] {desc}...")
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(f"FAILED: {' '.join(cmd)}\n")
        if proc.stdout:
            sys.stderr.write(proc.stdout + "\n")
        if proc.stderr:
            sys.stderr.write(proc.stderr + "\n")
        raise SystemExit(proc.returncode)
    return proc.stdout


def _find_tool(name: str, candidates: list[Path]) -> str:
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(f"Required tool '{name}' not found")


def _find_sdk() -> tuple[Path, Path, Path]:
    sdk_roots = []
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        v = os.environ.get(var)
        if v:
            sdk_roots.append(Path(v))
    sdk_roots.extend([Path("/opt/android-sdk"), Path("/usr/local/lib/android/sdk")])
    for sdk in sdk_roots:
        plats = sdk / "platforms"
        bts = sdk / "build-tools"
        if not plats.is_dir() or not bts.is_dir():
            continue
        jars = sorted(plats.glob("android-*/android.jar"), reverse=True)
        bt_dirs = sorted([d for d in bts.iterdir() if d.is_dir()], reverse=True)
        for bt in bt_dirs:
            if (bt / "lib" / "d8.jar").is_file() and (bt / "aapt").is_file() and jars:
                return (jars[0], bt / "lib" / "d8.jar", bt)
    raise FileNotFoundError("Android SDK (android.jar + build-tools) not found")


def _find_ndk_bin() -> Path:
    cands = []
    for var in ("ANDROID_NDK_HOME", "ANDROID_NDK_ROOT"):
        v = os.environ.get(var)
        if v:
            cands.append(Path(v))
    for sdk_var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        v = os.environ.get(sdk_var)
        if v and (Path(v) / "ndk").is_dir():
            cands.extend(sorted((Path(v) / "ndk").iterdir(), reverse=True))
    ndk_dir = Path("/opt/android-sdk/ndk")
    if ndk_dir.is_dir():
        cands.extend(sorted(ndk_dir.iterdir(), reverse=True))
    for ndk in cands:
        b = ndk / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin"
        if (b / "aarch64-linux-android26-clang").is_file():
            return b
    raise FileNotFoundError("Android NDK prebuilt toolchain not found (expected /opt/android-sdk/ndk/*)")


def _find_mojo() -> str:
    return _find_tool("mojo", [Path("/tmp/mojoenv/bin/mojo"), Path.home() / ".pixi" / "bin" / "mojo"])


def _write_png(path: Path, width: int, height: int, rgba_rows: list[bytes]) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = bytearray()
    for row in rgba_rows:
        raw.append(0)
        raw.extend(row)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def _generate_mojo_icon(path: Path, size: int) -> None:
    """Render an obsidian + glowing Mojo cyan/blue voxel cube app icon matching the banner."""
    rows: list[bytes] = []
    cx = (size - 1) * 0.5
    cy = (size - 1) * 0.5
    half_box = size * 0.5 - size * 0.08
    corner_r = size * 0.22
    for y in range(size):
        row = bytearray()
        dy = abs(y - cy)
        for x in range(size):
            dx = abs(x - cx)
            in_card = True
            if dx > half_box or dy > half_box:
                in_card = False
            elif dx > half_box - corner_r and dy > half_box - corner_r:
                qx = dx - (half_box - corner_r)
                qy = dy - (half_box - corner_r)
                if qx * qx + qy * qy > corner_r * corner_r:
                    in_card = False
            if not in_card:
                row.extend((0, 0, 0, 0))
                continue

            vx = int((x * 8) // size)
            vy = int((y * 8) // size)
            checker = ((vx + vy) & 1) * 6
            r, g, b = 10 + checker, 18 + checker, 34 + checker
            edge_dist = max(dx, dy)
            if edge_dist > half_box - max(2.0, size * 0.055):
                r, g, b = 56, 189, 248  # Mojo electric sky-blue frame
            else:
                nx = (x - cx) / max(1.0, half_box)
                ny = (y - cy) / max(1.0, half_box)
                mand = abs(nx) + abs(ny)
                if mand < 0.64:
                    # Stylized "M" inside diamond crystal core
                    in_m = (
                        abs(nx) > 0.16 and abs(nx) < 0.29 and abs(ny) < 0.28
                    ) or (abs(abs(nx) - abs(ny - 0.02)) < 0.09 and ny < 0.06 and abs(nx) < 0.24)
                    if in_m:
                        r, g, b = 235, 250, 255
                    else:
                        shade = int((1.0 - mand) * 50)
                        r, g, b = min(255, 14 + shade), min(255, 145 + shade), min(255, 225 + shade // 2)
                elif mand < 0.74:
                    r, g, b = 12, 74, 110
            row.extend((r, g, b, 255))
        rows.append(bytes(row))
    _write_png(path, size, size, rows)


def _prepare_resources(build_res: Path) -> None:
    if build_res.exists():
        shutil.rmtree(build_res)
    shutil.copytree(ANDROID_DIR / "res", build_res)
    for folder, px in (("mipmap-mdpi", 48), ("mipmap-hdpi", 72), ("mipmap-xhdpi", 96), ("mipmap-xxhdpi", 144), ("mipmap-xxxhdpi", 192)):
        _generate_mojo_icon(build_res / folder / "ic_launcher.png", px)

    banner_dst = build_res / "drawable" / "mojo_edition_banner.png"
    banner_dst.parent.mkdir(parents=True, exist_ok=True)
    if BRAND_BANNER.is_file():
        shutil.copy2(BRAND_BANNER, banner_dst)
    elif Path("/home/antigravity/file.png").is_file():
        shutil.copy2(Path("/home/antigravity/file.png"), banner_dst)
    else:
        _generate_mojo_icon(banner_dst, 192)


def _shizuku_jars(build_dir: Path) -> list[Path]:
    out = []
    for art, sha1 in SHIZUKU_AARS.items():
        aar = CACHE_ROOT / f"shizuku-{art}-{SHIZUKU_VERSION}.aar"
        if not aar.is_file():
            aar.parent.mkdir(parents=True, exist_ok=True)
            url = f"https://repo1.maven.org/maven2/dev/rikka/shizuku/{art}/{SHIZUKU_VERSION}/{art}-{SHIZUKU_VERSION}.aar"
            print(f"[muffin-apk] fetching {art}-{SHIZUKU_VERSION}.aar...")
            with urllib.request.urlopen(url, timeout=120) as r:
                aar.write_bytes(r.read())
        got = hashlib.sha1(aar.read_bytes()).hexdigest()
        if got != sha1:
            aar.unlink()
            raise SystemExit(f"{aar.name} SHA-1 {got} != expected {sha1}")
        jar = build_dir / f"shizuku-{art}.jar"
        with zipfile.ZipFile(aar) as z:
            jar.write_bytes(z.read("classes.jar"))
        out.append(jar)
    return out


def _ensure_keystore(keytool: str) -> None:
    if KEYSTORE_PATH.is_file():
        return
    KEYSTORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _run(
        "Generating release signing keystore",
        [
            keytool, "-genkeypair", "-v",
            "-keystore", str(KEYSTORE_PATH), "-storepass", KEY_PASS,
            "-alias", KEY_ALIAS, "-keypass", KEY_PASS,
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-dname", "CN=Muffin Minecraft Mojo Edition, OU=Muffin, O=Muffin, L=Istanbul, C=TR",
        ],
    )


def build_apk(out_apk: Path) -> Path:
    android_jar, d8_jar, bt_bin = _find_sdk()
    ndk_bin = _find_ndk_bin()
    mojo = _find_mojo()
    aapt = _find_tool("aapt", [bt_bin / "aapt"])
    zipalign = _find_tool("zipalign", [bt_bin / "zipalign"])
    apksigner = _find_tool("apksigner", [bt_bin / "apksigner"])
    java_home = Path(os.environ.get("JAVA_HOME", "/usr/lib/jvm/java-21-openjdk-amd64"))
    javac = _find_tool("javac", [java_home / "bin" / "javac"])
    java = _find_tool("java", [java_home / "bin" / "java"])
    keytool = _find_tool("keytool", [java_home / "bin" / "keytool"])

    build_dir = REPO_ROOT / "work" / "android_build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    build_res = build_dir / "res"
    gen_dir = build_dir / "gen"
    classes_dir = build_dir / "classes"
    dex_dir = build_dir / "dex"
    for d in (gen_dir, classes_dir, dex_dir):
        d.mkdir(parents=True, exist_ok=True)

    _prepare_resources(build_res)

    # Compile Mojo 1.2 Core + NDK JNI C shim for both arm64-v8a & x86_64
    built_libs: list[tuple[str, Path]] = []
    for abi, triple, clang_name in ABIS:
        abi_dir = build_dir / "lib" / abi
        abi_dir.mkdir(parents=True, exist_ok=True)
        mojo_obj = build_dir / f"muffin_core_{abi}.o"
        c_obj = build_dir / f"muffin_jni_{abi}.o"
        so_path = abi_dir / "libmuffin_core.so"

        _run(
            f"Compiling Mojo 1.2 Native Core ({abi})",
            [mojo, "build", "--emit", "object", "--target-triple", triple,
             str(ANDROID_DIR / "jni" / "muffin_core.mojo"), "-o", str(mojo_obj)],
        )
        _run(
            f"Compiling Android JNI + GLES3 shim ({abi})",
            [str(ndk_bin / clang_name), "-c", "-O2", "-fPIC", "-Wall", "-Wextra",
             str(ANDROID_DIR / "jni" / "muffin_android_jni.c"), "-o", str(c_obj)],
        )
        _run(
            f"Linking libmuffin_core.so ({abi}, 16KB page-aligned)",
            [str(ndk_bin / clang_name), "-shared", "-Wl,-z,max-page-size=16384",
             "-Wl,--gc-sections", "-Wl,-soname,libmuffin_core.so",
             str(mojo_obj), str(c_obj),
             "-landroid", "-llog", "-ldl", "-lm", "-o", str(so_path)],
        )
        built_libs.append((abi, so_path))

    base_ap_ = build_dir / "base.ap_"
    _run(
        "Packaging resources & AndroidManifest via aapt",
        [aapt, "package", "-f", "-m",
         "-M", str(ANDROID_DIR / "AndroidManifest.xml"),
         "-S", str(build_res),
         "-I", str(android_jar),
         "-J", str(gen_dir),
         "-F", str(base_ap_)],
    )

    java_sources = [str(p) for p in sorted(gen_dir.rglob("*.java"))]
    java_sources += [str(p) for p in sorted((ANDROID_DIR / "src").rglob("*.java"))]
    lib_jars = _shizuku_jars(build_dir)
    _run(
        f"Compiling {len(java_sources)} Java sources (--release 17)",
        [javac, "--release", "17", "-encoding", "UTF-8",
         "-cp", os.pathsep.join([str(android_jar), *map(str, lib_jars)]),
         "-d", str(classes_dir), *java_sources],
    )

    class_files = [str(p) for p in sorted(classes_dir.rglob("*.class"))]
    _run(
        f"Dexing {len(class_files)} classes via D8",
        [java, "-Xmx512m", "-cp", str(d8_jar), "com.android.tools.r8.D8",
         "--release", "--min-api", "26",
         "--lib", str(android_jar), "--output", str(dex_dir),
         *class_files, *map(str, lib_jars)],
    )

    unsigned_apk = build_dir / "unsigned.apk"
    shutil.copy2(base_ap_, unsigned_apk)
    with zipfile.ZipFile(unsigned_apk, "a") as zf:
        for dex in sorted(dex_dir.glob("*.dex")):
            zf.write(dex, dex.name, compress_type=zipfile.ZIP_DEFLATED)
        for abi, so_path in built_libs:
            zf.write(so_path, f"lib/{abi}/libmuffin_core.so", compress_type=zipfile.ZIP_STORED)

    aligned_apk = build_dir / "aligned.apk"
    _run("Aligning APK (16KB page boundary)", [zipalign, "-f", "-P", "16", "4", str(unsigned_apk), str(aligned_apk)])

    _ensure_keystore(keytool)
    out_apk.parent.mkdir(parents=True, exist_ok=True)
    _run(
        "Signing APK with apksigner",
        [apksigner, "sign", "--ks", str(KEYSTORE_PATH), "--ks-key-alias", KEY_ALIAS,
         "--ks-pass", f"pass:{KEY_PASS}", "--key-pass", f"pass:{KEY_PASS}",
         "--out", str(out_apk), str(aligned_apk)],
    )
    ver = _run("Verifying signed APK", [apksigner, "verify", "--verbose", "--print-certs", str(out_apk)])
    print(ver.strip())
    mb = out_apk.stat().st_size / (1024.0 * 1024.0)
    print(f"[muffin-apk] SUCCESS: {out_apk} ({mb:.2f} MiB)")
    return out_apk


def main() -> int:
    ap = argparse.ArgumentParser(description="Build Muffin Minecraft Mojo Edition Android APK")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT_APK)
    args = ap.parse_args()
    build_apk(args.out.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
