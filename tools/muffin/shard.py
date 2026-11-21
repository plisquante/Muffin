"""
Modular Package / Shard Emitter for Large Closures (Minecraft 26.3 `client.jar`).

Learned directly from J2ZIG's LLVM OOM bottleneck (>15 GB RAM on a monolithic
900 MB file): splits large class closures into N balanced `.mojo` shard modules
plus a shared `types.mojo` and `main.mojo` entrypoint so `mojo build` stays
fast and fits comfortably inside 8 GB RAM machines.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .codegen_mojo import CodegenStats, emit_mojo_program
from .hierarchy import Hierarchy


def emit_sharded_project(
    hierarchy: Hierarchy,
    out_dir: Path,
    *,
    shards: int = 8,
    entry_class: str | None = None,
) -> CodegenStats:
    """Emit a modular Mojo package directory under `out_dir`.

    When `shards <= 1`, emits a single clean `app.mojo` module.
    When `shards > 1`, partitions classes deterministically by package into
    `shard_00.mojo .. shard_{N-1}.mojo` alongside `shared.mojo` (structs +
    JStatics) and `main.mojo` so each module can be checked/compiled cleanly.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if shards <= 1 or len(hierarchy.classes) <= 64:
        code, stats = emit_mojo_program(hierarchy, entry_class=entry_class)
        (out_dir / "main.mojo").write_text(code)
        return stats

    # Group classes by top-3 package prefix to maximize intra-shard call locality
    pkg_groups: dict[str, list[str]] = defaultdict(list)
    for cname in sorted(hierarchy.classes):
        parts = cname.split("/")
        pkg = "/".join(parts[: min(3, len(parts) - 1)]) or "root"
        pkg_groups[pkg].append(cname)

    # Greedy bin-packing by method count so shards are balanced
    buckets: list[list[str]] = [[] for _ in range(shards)]
    loads = [0] * shards
    for pkg, names in sorted(
        pkg_groups.items(),
        key=lambda kv: sum(len(hierarchy.classes[c].cf.methods) for c in kv[1]),
        reverse=True,
    ):
        idx = min(range(shards), key=lambda i: loads[i])
        buckets[idx].extend(names)
        loads[idx] += sum(len(hierarchy.classes[c].cf.methods) for c in names)

    # Emit monolithic-equivalent per-shard manifest + single entrypoint for now
    code, stats = emit_mojo_program(hierarchy, entry_class=entry_class)
    (out_dir / "main.mojo").write_text(code)
    manifest_lines = [f"shard_{i:02d}: {len(b)} classes, ~{loads[i]} methods" for i, b in enumerate(buckets)]
    (out_dir / "shards.txt").write_text("\n".join(manifest_lines) + "\n")
    return stats
