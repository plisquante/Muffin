#!/usr/bin/env python3
"""
Muffin - fetch_client.py

Mojang'in resmi sunucularindan Minecraft Java Edition client.jar'ini (ve
istege bagli olarak bagimli kutuphaneleri) indirir, SHA-1 ile dogrular.

Downloads the official Minecraft: Java Edition client.jar (and optionally the
libraries it depends on) straight from Mojang's servers and verifies SHA-1.

IMPORTANT / ONEMLI:
    Indirilen dosyalar ve bunlardan uretilen her sey (cevrilmis Mojo kodu dahil)
    `work/` altina yazilir ve ASLA repoya commit edilmez. Minecraft kodu Mojang'in
    mulkiyetindedir; Muffin yalnizca donusturucu araclari dagitir.

Usage:
    python3 tools/fetch_client.py                  # latest release
    python3 tools/fetch_client.py --version 26.3
    python3 tools/fetch_client.py --snapshot       # latest snapshot
    python3 tools/fetch_client.py --with-libraries # also fetch library jars
    python3 tools/fetch_client.py --list           # list available versions

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORK = REPO_ROOT / "work"
USER_AGENT = "Muffin-fetch/0.1 (+https://github.com/plisquante/Muffin)"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _get_json(url: str) -> dict:
    return json.loads(_get(url))


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, sha1: str | None, size: int | None = None, retries: int = 4) -> bool:
    """Download `url` to `dest` unless an identical file already exists.

    Returns True if a download happened, False if the cached file was reused.
    Transient network errors are retried with exponential backoff.
    """
    if dest.exists() and sha1 and _sha1(dest) == sha1:
        return False
    for attempt in range(retries):
        try:
            return _download_once(url, dest, sha1, size)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(f"[muffin] {dest.name}: {e} - retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    return False


def _download_once(url: str, dest: Path, sha1: str | None, size: int | None) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    h = hashlib.sha1()
    done = 0
    with urllib.request.urlopen(req, timeout=60) as r, tmp.open("wb") as out:
        total = size or int(r.headers.get("Content-Length") or 0)
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if total and sys.stderr.isatty():
                pct = done * 100 // total
                sys.stderr.write(f"\r  {dest.name}: {pct:3d}% ({done >> 20} MiB)")
    if sys.stderr.isatty() and size:
        sys.stderr.write("\n")
    if sha1 and h.hexdigest() != sha1:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-1 mismatch for {url}: got {h.hexdigest()}, want {sha1}")
    os.replace(tmp, dest)
    return True


def _current_os() -> str:
    s = platform.system().lower()
    return {"darwin": "osx", "windows": "windows"}.get(s, "linux")


def _rules_allow(rules: list[dict] | None, os_name: str) -> bool:
    """Evaluate Mojang launcher library rules (subset: os.name only)."""
    if not rules:
        return True
    allowed = False
    for rule in rules:
        os_rule = rule.get("os")
        if os_rule is None or os_rule.get("name") == os_name:
            allowed = rule.get("action") == "allow"
    return allowed


def resolve_version(manifest: dict, version: str | None, snapshot: bool) -> dict:
    if version is None:
        version = manifest["latest"]["snapshot" if snapshot else "release"]
    for v in manifest["versions"]:
        if v["id"] == version:
            return v
    raise SystemExit(f"error: version '{version}' not found in manifest (try --list)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Download Minecraft client.jar for Muffin.")
    ap.add_argument("--version", "-v", help="version id, e.g. 26.3 (default: latest release)")
    ap.add_argument("--snapshot", action="store_true", help="use latest snapshot instead of release")
    ap.add_argument("--with-libraries", action="store_true", help="also download library jars")
    ap.add_argument("--work", type=Path, default=DEFAULT_WORK, help="output directory (default: work/)")
    ap.add_argument("--list", action="store_true", help="list versions and exit")
    args = ap.parse_args(argv)

    manifest = _get_json(MANIFEST_URL)
    if args.list:
        for v in manifest["versions"][:50]:
            print(f"{v['id']:<24} {v['type']:<9} {v['releaseTime']}")
        return 0

    entry = resolve_version(manifest, args.version, args.snapshot)
    vid = entry["id"]
    vdir = args.work / "versions" / vid
    vdir.mkdir(parents=True, exist_ok=True)

    meta_path = vdir / f"{vid}.json"
    download(entry["url"], meta_path, entry.get("sha1"))
    meta = json.loads(meta_path.read_text())

    client = meta["downloads"]["client"]
    jar_path = vdir / "client.jar"
    fresh = download(client["url"], jar_path, client["sha1"], client.get("size"))
    print(f"[muffin] client.jar {vid}: {'downloaded' if fresh else 'cached'} -> {jar_path}")

    # Pre-26.1 versions are obfuscated and ship official ProGuard mappings.
    # 26.1+ ships unobfuscated, so there is nothing to fetch.
    mappings = meta["downloads"].get("client_mappings")
    if mappings:
        mp = vdir / "client_mappings.txt"
        download(mappings["url"], mp, mappings["sha1"], mappings.get("size"))
        print(f"[muffin] obfuscated build: mappings -> {mp}")
    else:
        print("[muffin] unobfuscated build (no mappings needed)")

    java = meta.get("javaVersion", {})
    print(f"[muffin] target Java: {java.get('majorVersion')}  main: {meta.get('mainClass')}")

    if args.with_libraries:
        os_name = _current_os()
        libdir = args.work / "libraries"
        n = 0
        for lib in meta.get("libraries", []):
            if not _rules_allow(lib.get("rules"), os_name):
                continue
            art = lib.get("downloads", {}).get("artifact")
            if not art:
                continue
            download(art["url"], libdir / art["path"], art["sha1"], art.get("size"))
            n += 1
        print(f"[muffin] {n} libraries for {os_name} -> {libdir}")

    # Small machine-readable pointer for the rest of the toolchain.
    (args.work / "current.json").write_text(
        json.dumps({"version": vid, "client_jar": str(jar_path), "meta": str(meta_path)}, indent=2)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
