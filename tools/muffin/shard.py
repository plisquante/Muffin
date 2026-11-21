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
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .classfile import Member
from .codegen_mojo import (
    BUILTIN_CALLS,
    CodegenStats,
    _emit_method,
    _emit_runtime_imports,
    _resolve_static_owner,
    compress_cid_runs,
    emit_cross_trampoline,
    emit_mojo_program,
    emit_shard_classes_and_statics,
    emit_shared_module,
    emit_vdisp_function,
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
    reachable_methods: set[tuple[str, str, str]] | None = None,
) -> tuple[CodegenStats, list[Path]]:
    """Emit minimal `shared.mojo`, decoupled `shard_XX.mojo` files, and `main.mojo`."""
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

    # 2. Pre-lift all methods & compute inter-procedural Escape Analysis
    lifted_by_shard: list[list[tuple[ClassInfo, Member, MethodIR]]] = [[] for _ in range(actual_shards)]
    all_irs: list[MethodIR] = []
    for s_idx, b in enumerate(buckets):
        for cname in sorted(b):
            ci = hierarchy.classes[cname]
            for m in ci.cf.methods:
                if m.code is None:
                    continue
                if reachable_methods is not None and (cname, m.name, m.descriptor) not in reachable_methods:
                    continue
                ir = lift_method(ci.cf, m)
                lifted_by_shard[s_idx].append((ci, m, ir))
                all_irs.append(ir)

    non_escaping_params = compute_non_escaping_params(all_irs)

    # 3. Scan cross-shard accesses, large interfaces, and sharded virtual dispatchers
    cross_news: set[str] = set()
    cross_gf: set[tuple[str, str]] = set()
    cross_pf: set[tuple[str, str]] = set()
    cross_gs: set[tuple[str, str]] = set()
    cross_ps: set[tuple[str, str]] = set()
    cross_calls: set[tuple[str, str, str]] = set()
    vdisp_to_shard: dict[tuple[str, str, str], int] = {}
    vdisp_by_shard: list[list[tuple[str, str, str]]] = [[] for _ in range(actual_shards)]

    for cname, cci in hierarchy.classes.items():
        for mm in cci.cf.methods:
            if mm.name == "<clinit>" and mm.code is not None:
                if reachable_methods is None or (cname, "<clinit>", "()V") in reachable_methods:
                    cross_calls.add((cname, "<clinit>", "()V"))
    if entry_class and entry_class in hierarchy.classes:
        cross_calls.add((entry_class, "main", "([Ljava/lang/String;)V"))

    for s_idx in range(actual_shards):
        for _, _, ir in lifted_by_shard[s_idx]:
            for blk in ir.blocks.values():
                for st in blk.stmts:
                    op, a = st.op, st.args
                    if op == "new":
                        cn = a[0]
                        if cn in class_to_shard and class_to_shard[cn] != s_idx:
                            cross_news.add(cn)
                    elif op in ("instanceof", "checkcast"):
                        tc = a[0]
                        if tc not in stats.large_ifaces:
                            tci = hierarchy.classes.get(tc)
                            if tci is not None and tci.cf.is_interface:
                                ids = hierarchy.interface_impl_ids.get(tc, [])
                                if len(compress_cid_runs(ids)) > 16:
                                    stats.large_ifaces.add(tc)
                    elif op == "getfield":
                        own, fn, _ = a[0], a[1], a[2]
                        if own in class_to_shard and class_to_shard[own] != s_idx:
                            cross_gf.add((own, fn))
                    elif op == "putfield":
                        own, fn, _ = a[0], a[1], a[2]
                        if own in class_to_shard and class_to_shard[own] != s_idx:
                            cross_pf.add((own, fn))
                    elif op == "getstatic":
                        decl = _resolve_static_owner(hierarchy, a[0], a[1])
                        if decl is not None and class_to_shard.get(decl) != s_idx:
                            cross_gs.add((decl, a[1]))
                    elif op == "putstatic":
                        decl = _resolve_static_owner(hierarchy, a[0], a[1])
                        if decl is not None and class_to_shard.get(decl) != s_idx:
                            cross_ps.add((decl, a[1]))
                    elif op == "call":
                        cop, owner, mn, md, _, _ = a
                        if (owner, mn, md) in BUILTIN_CALLS:
                            continue
                        if cop in ("invokevirtual", "invokeinterface"):
                            devirt = hierarchy.resolve_devirtualized(owner, mn, md)
                            if devirt is not None:
                                r_own = hierarchy.resolve_method(devirt, mn, md)
                                if r_own and class_to_shard.get(r_own) != s_idx:
                                    cross_calls.add((r_own, mn, md))
                            else:
                                subs = hierarchy.concrete_targets(owner, mn, md)
                                if subs and len(subs) > 8:
                                    vkey = (owner, mn, md)
                                    if vkey not in vdisp_to_shard:
                                        vs = zlib.crc32(f"{owner}.{mn}{md}".encode()) % actual_shards
                                        vdisp_to_shard[vkey] = vs
                                        vdisp_by_shard[vs].append(vkey)
                                        for _, impl in subs:
                                            if class_to_shard.get(impl) != vs:
                                                cross_calls.add((impl, mn, md))
                                elif subs:
                                    for _, impl in subs:
                                        if class_to_shard.get(impl) != s_idx:
                                            cross_calls.add((impl, mn, md))
                                else:
                                    r_own = hierarchy.resolve_method(owner, mn, md)
                                    if r_own and class_to_shard.get(r_own) != s_idx:
                                        cross_calls.add((r_own, mn, md))
                        else:
                            r_own = hierarchy.resolve_method(owner, mn, md)
                            if r_own and class_to_shard.get(r_own) != s_idx:
                                cross_calls.add((r_own, mn, md))

    stats.vdisp_to_shard = vdisp_to_shard

    # 4. Emit minimal shared.mojo (only JStatics + large-interface binary helpers)
    shared_src, exported_syms = emit_shared_module(
        hierarchy, stats, num_shards=actual_shards, large_ifaces=stats.large_ifaces
    )
    (out_dir / "shared.mojo").write_text(shared_src)
    shared_import = f"from shared import {', '.join(exported_syms)}"

    # 5. Emit each decoupled shard_XX.mojo
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
        emit_shard_classes_and_statics(
            out,
            hierarchy,
            buckets[s_idx],
            s_idx,
            stats,
            cross_news=cross_news,
            cross_gf=cross_gf,
            cross_pf=cross_pf,
            cross_gs=cross_gs,
            cross_ps=cross_ps,
        )
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
            if (ir.owner, ir.name, ir.descriptor) in cross_calls:
                emit_cross_trampoline(out, ir)
        for v_owner, v_mname, v_mdesc in sorted(vdisp_by_shard[s_idx]):
            emit_vdisp_function(out, hierarchy, v_owner, v_mname, v_mdesc, stats, export_c_abi=True)
        sp = out_dir / f"shard_{s_idx:02d}.mojo"
        sp.write_text("\n".join(out))
        shard_paths.append(sp)

    # 6. Emit main.mojo (invokes <clinit>s and entry main via C-ABI trampolines)
    m_out: list[str] = []
    mw = m_out.append
    mw('"""Muffin Sharded Entrypoint (Mojo 1.2)."""')
    mw("")
    _emit_runtime_imports(mw)
    mw(shared_import)
    mw("")
    mw("def _run_muffin_entry() raises:")
    mw(f"    var statics = alloc_raw[JStatics]()")
    mw(f"    statics.unsafe_write(JStatics({actual_shards}))")
    for cname in sorted(hierarchy.classes):
        if (cname, "<clinit>", "()V") in cross_calls:
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


def _compile_obj(
    mojo_bin: str,
    repo_root: Path,
    out_dir: Path,
    src: Path,
    target_triple: str | None = None,
    opt_level: int = 3,
) -> Path:
    import time as _t

    obj = src.with_suffix(".o")
    cmd = [mojo_bin, "build", "--emit", "object", "-O", str(opt_level)]
    if target_triple:
        cmd.extend(["--target-triple", target_triple])
    cmd.extend(["-I", str(repo_root), "-I", str(out_dir), str(src), "-o", str(obj)])
    t0 = _t.monotonic()
    res = subprocess.run(cmd, capture_output=True, text=True)
    dt = _t.monotonic() - t0
    if res.returncode != 0:
        print(f"[muffin] FAILED compiling {src.name} ({dt:.1f}s):\n{res.stdout}\n{res.stderr}", flush=True)
        raise subprocess.CalledProcessError(res.returncode, cmd, res.stdout, res.stderr)
    print(f"[muffin] compiled {src.name} -> {obj.name} ({dt:.1f}s, {obj.stat().st_size / 1024:.1f} KiB)", flush=True)
    return obj


def compile_sharded(
    out_dir: Path,
    repo_root: Path,
    mojo_bin: str,
    exe_path: Path,
    *,
    jobs: int | None = None,
    target_triple: str | None = None,
    opt_level: int = 3,
) -> None:
    """Compile each `shard_XX.mojo` to `.o` in parallel and link with `main.mojo`."""
    shard_files = sorted(out_dir.glob("shard_*.mojo"))
    workers = max(1, jobs or min(len(shard_files), os.cpu_count() or 2))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        obj_files = list(
            ex.map(lambda sf: _compile_obj(mojo_bin, repo_root, out_dir, sf, target_triple, opt_level), shard_files)
        )

    cmd = [mojo_bin, "build", "-O", str(opt_level)]
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
    opt_level: int = 3,
) -> Path:
    """Compile all shards + main.mojo to Android `.o` objects in parallel and link 16KB-aligned `.so`."""
    src_files = sorted(out_dir.glob("shard_*.mojo")) + [out_dir / "main.mojo"]
    workers = max(1, jobs or min(len(src_files), os.cpu_count() or 2))
    print(f"[muffin] compiling {len(src_files)} Mojo shards (-O{opt_level}, jobs={workers})...", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        obj_files = list(
            ex.map(lambda sf: _compile_obj(mojo_bin, repo_root, out_dir, sf, target_triple, opt_level), src_files)
        )

    so_path.parent.mkdir(parents=True, exist_ok=True)
    link_cmd = [
        ndk_clang,
        "-shared",
        f"-O{min(2, max(0, opt_level))}",
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
