"""
Muffin CLI.

    python3 -m tools.muffin analyze [--jar work/versions/<v>/client.jar]
    python3 -m tools.muffin dump <internal/class/Name> [--jar ...]
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORK = REPO_ROOT / "work"


def _default_jar() -> Path:
    cur = WORK / "current.json"
    if not cur.exists():
        sys.exit("error: no client.jar yet - run `python3 tools/fetch_client.py` first")
    return Path(json.loads(cur.read_text())["client_jar"])


def cmd_analyze(args: argparse.Namespace) -> int:
    from .analyze import run

    jars = [Path(j) for j in args.jar] if args.jar else [_default_jar()]
    for jar in jars:
        rep = run(jar, WORK / "analysis")
        print(
            f"[muffin] {jar.name}: {rep.classes:,} classes, {rep.methods:,} methods, "
            f"{rep.instructions:,} insns, {sum(rep.indy.values()):,} indy -> work/analysis/{jar.stem}.md"
        )
    return 0


def cmd_dump(args: argparse.Namespace) -> int:
    from .bytecode import decode
    from .classfile import parse_class

    jar = Path(args.jar) if args.jar else _default_jar()
    with zipfile.ZipFile(jar) as z:
        cf = parse_class(z.read(args.cls.replace(".", "/") + ".class"))
    print(f"class {cf.name} extends {cf.super_name} implements {cf.interfaces}  (v{cf.major})")
    for f in cf.fields:
        print(f"  field 0x{f.access:04x} {f.name}: {f.descriptor}")
    for m in cf.methods:
        print(f"  method 0x{m.access:04x} {m.name}{m.descriptor}")
        if m.code and args.code:
            for insn in decode(m.code.code, cf.cp):
                print(f"      {insn}")
    return 0


def cmd_cfg(args: argparse.Namespace) -> int:
    """Count methods whose CFG is irreducible (cannot be structured without a dispatch loop)."""
    from .cfg import build_cfg, is_reducible
    from .classfile import parse_class

    jar = Path(args.jar) if args.jar else _default_jar()
    total = irreducible = 0
    bad: list[str] = []
    with zipfile.ZipFile(jar) as z:
        for n in z.namelist():
            if not n.endswith(".class") or n.startswith("META-INF/"):
                continue
            cf = parse_class(z.read(n))
            for m in cf.methods:
                if m.code is None:
                    continue
                total += 1
                if not is_reducible(build_cfg(cf, m)):
                    irreducible += 1
                    bad.append(f"{cf.name}.{m.name}{m.descriptor}")
    print(f"[muffin] {jar.name}: {total:,} methods, {irreducible:,} irreducible CFGs")
    for b in bad[:50]:
        print("   ", b)
    return 0


def cmd_translate(args: argparse.Namespace) -> int:
    """Translate a jar (or directory of .class files) to Mojo 1.2."""
    from .classfile import parse_class
    from .codegen_mojo import emit_mojo_program
    from .hierarchy import Hierarchy
    from .indy import IndyDesugarer
    from .shard import emit_sharded_project

    src = Path(args.input) if args.input else _default_jar()
    cfs = []
    if src.is_dir():
        for p in sorted(src.rglob("*.class")):
            cfs.append(parse_class(p.read_bytes()))
    else:
        prefixes = tuple(p.replace(".", "/") for p in (args.filter or []))
        with zipfile.ZipFile(src) as z:
            for n in z.namelist():
                if not n.endswith(".class") or n.startswith("META-INF/"):
                    continue
                if prefixes and not n.startswith(prefixes):
                    continue
                cfs.append(parse_class(z.read(n)))

    des = IndyDesugarer()
    for cf in cfs:
        des.rewrite_class(cf)
    cfs.extend(des.synthetic_classes)

    h = Hierarchy.build(cfs)
    entry = args.entry.replace(".", "/") if args.entry else None
    out_path = Path(args.out)

    if args.shards and args.shards > 1:
        stats, files = emit_sharded_project(h, out_path, shards=args.shards, entry_class=entry)
        print(
            f"[muffin] emitted {len(files)} sharded files in {out_path}: {stats.classes_emitted} classes, "
            f"{stats.methods_emitted} methods ({stats.structured_methods} structured, "
            f"{stats.dispatch_methods} dispatch), {stats.arena_allocs_lowered} EA arena allocs, "
            f"{stats.calls_devirtualized} devirt / {stats.calls_virtual} virtual calls"
        )
        return 0

    code, stats = emit_mojo_program(h, entry_class=entry)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(code)
    print(
        f"[muffin] emitted {out_path}: {stats.classes_emitted} classes, "
        f"{stats.methods_emitted} methods ({stats.structured_methods} structured, "
        f"{stats.dispatch_methods} dispatch), {stats.arena_allocs_lowered} EA arena allocs, "
        f"{stats.calls_devirtualized} devirt / {stats.calls_virtual} virtual calls"
    )
    return 0


def cmd_rta(args: argparse.Namespace) -> int:
    """Run closed-world Rapid Type Analysis across client.jar + libraries + JDK."""
    from .classpath import ClassPath
    from .rta import run_rta

    jar = Path(args.jar) if args.jar else _default_jar()
    libs = sorted(str(p) for p in (WORK / "libraries").rglob("*.jar"))
    entries: list[str] = [str(jar), *libs]
    jmod = Path("/usr/lib/jvm/java-21-openjdk-amd64/jmods/java.base.jmod")
    boot_start = len(entries)
    if jmod.exists():
        entries.append(str(jmod))
    cp = ClassPath(entries, boot_start=boot_start)
    res = run_rta(cp, args.entry)
    print(
        f"[muffin] RTA from {args.entry}: loaded={len(res.loaded):,}, "
        f"instantiated={len(res.instantiated):,}, methods={len(res.reachable_methods):,}, "
        f"natives={len(res.native_methods):,}, missing={len(res.missing_classes):,}, "
        f"unsupported_indy={len(res.unsupported_indy)}"
    )
    return 0


def cmd_apk(args: argparse.Namespace) -> int:
    """Build dual-ABI (arm64-v8a + x86_64) signed Android Launcher APK."""
    from tools.build_apk import DEFAULT_OUT_APK, build_apk

    out = Path(args.out) if args.out else DEFAULT_OUT_APK
    build_apk(out.resolve())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="muffin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="static analysis of client.jar / libraries")
    a.add_argument("--jar", action="append", help="jar to analyze (repeatable)")
    a.set_defaults(fn=cmd_analyze)
    c = sub.add_parser("cfg", help="control-flow reducibility census")
    c.add_argument("--jar")
    c.set_defaults(fn=cmd_cfg)
    d = sub.add_parser("dump", help="javap-like dump of one class")
    d.add_argument("cls")
    d.add_argument("--jar")
    d.add_argument("--code", action="store_true", help="include bytecode")
    d.set_defaults(fn=cmd_dump)
    r = sub.add_parser("rta", help="closed-world Rapid Type Analysis")
    r.add_argument("--jar")
    r.add_argument("--entry", default="net/minecraft/client/main/Main")
    r.set_defaults(fn=cmd_rta)
    t = sub.add_parser("translate", help="translate a jar or class dir to Mojo 1.2")
    t.add_argument("--input", "-i", help="jar file or directory of .class files (default: current client.jar)")
    t.add_argument("--out", "-o", required=True, help="output .mojo path (or directory when --shards > 1)")
    t.add_argument("--entry", help="optional Java main class (e.g. demo.Main)")
    t.add_argument("--filter", action="append", help="only include classes matching prefix")
    t.add_argument("--shards", type=int, default=1, help="split output into N separately-compiled .mojo shards")
    t.set_defaults(fn=cmd_translate)
    k = sub.add_parser("apk", help="build dual-ABI Mojo Edition Android Launcher APK")
    k.add_argument("--out", help="output .apk path (default: releases/Muffin-Minecraft-Mojo-Edition.apk)")
    k.set_defaults(fn=cmd_apk)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
