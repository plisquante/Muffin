"""
Real Multi-Shard Native Compiler & Emitter (Mojo 1.2).

Splits large class closures (e.g., Minecraft 26.3's 43,618+ classes) into N
independently compiled `.mojo` shards (`shard_00.mojo .. shard_{N-1}.mojo`)
plus `shared.mojo` and `main.mojo`:
- Intra-shard calls remain direct Mojo `raises` calls (full LLVM inlining).
- Cross-shard calls go through `@export("x_...")` `abi("C")` trampolines with
  exception propagation via `JStatics.has_exc` and reference returns via
  `JStatics.ret_ref`.
- `compile_sharded` runs `mojo build --emit object` per shard and links all
  `.o` objects into one native executable via `mojo build main.mojo -Xlinker ...`.
"""

from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .classfile import Member
from .codegen_mojo import (
    CodegenStats,
    _emit_method,
    _emit_runtime_imports,
    emit_cross_trampoline,
    emit_mojo_program,
    emit_shared_module,
)
from .hierarchy import ClassInfo, Hierarchy
from .ir import MethodIR, lift_method
from .optimize import compute_non_escaping_params, optimize_method
from .structurer import DispatchNode, structure_method
from .types import mangle_method


def emit_sharded_project(
    hierarchy: Hierarchy,
    out_dir: Path,
    *,
    shards: int = 8,
    entry_class: str | None = None,
    shared_lib: bool = False,
) -> tuple[CodegenStats, list[Path]]:
    """Emit `shared.mojo`, `shard_XX.mojo`, and `main.mojo` into `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if (shards <= 1 or len(hierarchy.classes) <= 1) and not shared_lib:
        code, stats = emit_mojo_program(hierarchy, entry_class=entry_class)
        main_p = out_dir / "main.mojo"
        main_p.write_text(code)
        return stats, [main_p]

    # 1. Greedy package-grouped bin packing so calls within a package stay intra-shard
    pkg_groups: dict[str, list[str]] = defaultdict(list)
    for cname in sorted(hierarchy.classes):
        parts = cname.split("/")
        pkg = "/".join(parts[: min(3, len(parts) - 1)]) or "root"
        pkg_groups[pkg].append(cname)

    actual_shards = min(shards, max(1, len(pkg_groups)))
    buckets: list[list[str]] = [[] for _ in range(actual_shards)]
    loads = [0] * actual_shards
    for _, names in sorted(
        pkg_groups.items(),
        key=lambda kv: sum(len(hierarchy.classes[c].cf.methods) for c in kv[1]),
        reverse=True,
    ):
        idx = min(range(actual_shards), key=lambda i: loads[i])
        buckets[idx].extend(names)
        loads[idx] += sum(len(hierarchy.classes[c].cf.methods) for c in names)

    class_to_shard: dict[str, int] = {}
    for s_idx, b in enumerate(buckets):
        for cname in b:
            class_to_shard[cname] = s_idx

    stats = CodegenStats(current_shard=None, class_to_shard=class_to_shard)

    # 2. Emit shared.mojo
    shared_src, exported_syms = emit_shared_module(hierarchy, stats)
    (out_dir / "shared.mojo").write_text(shared_src)
    shared_import = f"from shared import {', '.join(exported_syms)}"

    # 3. Pre-lift all methods & compute inter-procedural Escape Analysis
    lifted_by_shard: list[list[tuple[ClassInfo, Member, MethodIR]]] = [[] for _ in range(actual_shards)]
    all_irs: list[MethodIR] = []
    for s_idx, b in enumerate(buckets):
        for cname in sorted(b):
            ci = hierarchy.classes[cname]
            for m in ci.cf.methods:
                if m.code is None:
                    continue
                ir = lift_method(ci.cf, m)
                lifted_by_shard[s_idx].append((ci, m, ir))
                all_irs.append(ir)

    non_escaping_params = compute_non_escaping_params(all_irs)

    # 4. Emit each shard_XX.mojo
    shard_paths: list[Path] = []
    for s_idx in range(actual_shards):
        stats.current_shard = s_idx
        out: list[str] = []
        w = out.append
        w(f'"""Muffin Shard {s_idx:02d} (Mojo 1.2)."""')
        w("")
        _emit_runtime_imports(w)
        w(shared_import)
        w("")
        for ci, m, ir in lifted_by_shard[s_idx]:
            opt = optimize_method(ir, non_escaping_params=non_escaping_params)
            ast = structure_method(ir)
            stats.methods_emitted += 1
            if isinstance(ast, DispatchNode):
                stats.dispatch_methods += 1
            else:
                stats.structured_methods += 1
            stats.arena_allocs_lowered += len(opt.arena_allocs)
            _emit_method(out, hierarchy, ci, m, ir, opt, ast, stats)
            w("")
            emit_cross_trampoline(out, ir)
        sp = out_dir / f"shard_{s_idx:02d}.mojo"
        sp.write_text("\n".join(out))
        shard_paths.append(sp)

    # 5. Emit main.mojo (invokes <clinit>s and entry main via C-ABI trampolines)
    m_out: list[str] = []
    mw = m_out.append
    mw('"""Muffin Sharded Entrypoint (Mojo 1.2)."""')
    mw("")
    _emit_runtime_imports(mw)
    mw(shared_import)
    mw("")
    mw("def _run_muffin_entry() raises:")
    mw("    var statics = alloc_raw[JStatics]()")
    mw("    statics.unsafe_write(JStatics())")
    for cname in sorted(hierarchy.classes):
        cci = hierarchy.classes[cname]
        if any(mm.name == "<clinit>" and mm.code is not None for mm in cci.cf.methods):
            fn_m = mangle_method(cname, "<clinit>", "()V")
            mw(f'    external_call["x_{fn_m}", NoneType](statics)')
            mw("    if statics[].has_exc:")
            mw("        statics[].has_exc = False")
            mw('        raise JThrowable(statics[].exc_cid, "clinit", statics[].exc_obj.copy())')
    if entry_class and entry_class in hierarchy.classes:
        ci = hierarchy.classes[entry_class]
        if any(mm.is_static() and mm.name == "main" and mm.descriptor == "([Ljava/lang/String;)V" for mm in ci.cf.methods):
            main_fn = mangle_method(entry_class, "main", "([Ljava/lang/String;)V")
            mw("    var argv = new_array[JRef](9, 0)")
            mw(f'    external_call["x_{main_fn}", NoneType](statics, argv.ptr)')
            mw("    if statics[].has_exc:")
            mw("        statics[].has_exc = False")
            mw('        raise JThrowable(statics[].exc_cid, "main", statics[].exc_obj.copy())')
    mw("    statics.unsafe_deinit_pointee()")
    mw("    free_raw(statics)")
    mw("")
    if shared_lib:
        mw('@export("mc_android_entry")')
        mw('def _mc_android_entry() abi("C") -> Int32:')
        mw("    try:")
        mw("        _run_muffin_entry()")
        mw("        return 0")
        mw("    except:")
        mw("        return -1")
        mw("")
        mw('@export("j2z_android_entry")')
        mw('def _j2z_android_entry() abi("C") -> Int32:')
        mw("    return _mc_android_entry()")
        mw("")
    else:
        mw("def main() raises:")
        mw("    _run_muffin_entry()")
        mw("")

    main_path = out_dir / "main.mojo"
    main_path.write_text("\n".join(m_out))
    shard_paths.append(main_path)
    return stats, shard_paths


def _compile_obj(mojo_bin: str, repo_root: Path, out_dir: Path, src: Path, target_triple: str | None = None) -> Path:
    obj = src.with_suffix(".o")
    cmd = [mojo_bin, "build", "--emit", "object"]
    if target_triple:
        cmd.extend(["--target-triple", target_triple])
    cmd.extend(["-I", str(repo_root), "-I", str(out_dir), str(src), "-o", str(obj)])
    subprocess.run(cmd, check=True)
    return obj


def compile_sharded(
    out_dir: Path,
    repo_root: Path,
    mojo_bin: str,
    exe_path: Path,
    *,
    jobs: int | None = None,
    target_triple: str | None = None,
) -> None:
    """Compile each `shard_XX.mojo` to `.o` in parallel and link with `main.mojo`."""
    shard_files = sorted(out_dir.glob("shard_*.mojo"))
    workers = max(1, jobs or min(len(shard_files), os.cpu_count() or 2))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        obj_files = list(ex.map(lambda sf: _compile_obj(mojo_bin, repo_root, out_dir, sf, target_triple), shard_files))

    cmd = [mojo_bin, "build"]
    if target_triple:
        cmd.extend(["--target-triple", target_triple])
    cmd.extend(["-I", str(repo_root), "-I", str(out_dir), str(out_dir / "main.mojo")])
    for of in obj_files:
        cmd.extend(["-Xlinker", str(of)])
    cmd.extend(["-o", str(exe_path)])
    subprocess.run(cmd, check=True)


def compile_sharded_shared_lib(
    out_dir: Path,
    repo_root: Path,
    mojo_bin: str,
    ndk_clang: str,
    so_path: Path,
    *,
    target_triple: str = "aarch64-unknown-linux-android26",
    jobs: int | None = None,
) -> Path:
    """Compile all shards + main.mojo to Android `.o` objects in parallel and link 16KB-aligned `.so`."""
    src_files = sorted(out_dir.glob("shard_*.mojo")) + [out_dir / "main.mojo"]
    workers = max(1, jobs or min(len(src_files), os.cpu_count() or 2))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        obj_files = list(ex.map(lambda sf: _compile_obj(mojo_bin, repo_root, out_dir, sf, target_triple), src_files))

    so_path.parent.mkdir(parents=True, exist_ok=True)
    link_cmd = [
        ndk_clang,
        "-shared",
        "-O2",
        "-Wl,-z,max-page-size=16384",
        "-Wl,--export-dynamic",
        *[str(o) for o in obj_files],
        "-llog",
        "-lm",
        "-ldl",
        "-o",
        str(so_path),
    ]
    subprocess.run(link_cmd, check=True)
    return so_path
