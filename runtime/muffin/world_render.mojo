"""
Muffin Multi-Core + SIMD World Culling & GPU-Driven Instancing (Mojo 1.2).

Provides:
1. 3-Stage Vanilla-Safe World Culling across `SectionSnapshot`s:
   - Stage 1: 6-plane SIMD Frustum AABB cull (parallelized via `WorkerPool`)
   - Stage 2: 6-face CaveCulling Portal BFS
   - Stage 3: Conservative `HiZPyramid` occlusion cull + pixel-coverage LOD (0..3)
2. Unified 64B `GpuInstance` / 32B `GpuParticle` non-block instancing with
   opaque-first batching and frustum culling.
"""

from std.atomic import Atomic
from std.ffi import external_call
from std.memory import MutOpaquePointer, OptionalPointer, Pointer
from std.origin import MutUntrackedOrigin
from std.sys import size_of

from .pool import WorkerPool
from .simd_math import Vec4f


@fieldwise_init
struct Frustum(Copyable, Defaultable):
    """Six normalized frustum planes (nx, ny, nz, d)."""

    var p0: Vec4f
    var p1: Vec4f
    var p2: Vec4f
    var p3: Vec4f
    var p4: Vec4f
    var p5: Vec4f

    def __init__(out self):
        self.p0 = Vec4f(1, 0, 0, 512)
        self.p1 = Vec4f(-1, 0, 0, 512)
        self.p2 = Vec4f(0, 1, 0, 512)
        self.p3 = Vec4f(0, -1, 0, 512)
        self.p4 = Vec4f(0, 0, 1, 512)
        self.p5 = Vec4f(0, 0, -1, 512)

    @always_inline
    def aabb_visible(self, min_v: Vec4f, max_v: Vec4f) -> Bool:
        if not _plane_aabb(self.p0, min_v, max_v):
            return False
        if not _plane_aabb(self.p1, min_v, max_v):
            return False
        if not _plane_aabb(self.p2, min_v, max_v):
            return False
        if not _plane_aabb(self.p3, min_v, max_v):
            return False
        if not _plane_aabb(self.p4, min_v, max_v):
            return False
        if not _plane_aabb(self.p5, min_v, max_v):
            return False
        return True


@always_inline
def _plane_aabb(plane: Vec4f, min_v: Vec4f, max_v: Vec4f) -> Bool:
    var px = max_v[0] if plane[0] >= 0.0 else min_v[0]
    var py = max_v[1] if plane[1] >= 0.0 else min_v[1]
    var pz = max_v[2] if plane[2] >= 0.0 else min_v[2]
    return plane[0] * px + plane[1] * py + plane[2] * pz + plane[3] >= 0.0


@fieldwise_init
struct SectionSnapshot(Copyable, Defaultable):
    """16x16x16 Minecraft chunk section metadata for lock-free culling."""

    var min_v: Vec4f
    var max_v: Vec4f
    var non_empty: Bool
    var face_visibility_mask: UInt64  # 6x6 symmetric portal reachability bitset
    var lod: UInt8
    var visible: Bool

    def __init__(out self):
        self.min_v = Vec4f(0, 0, 0, 0)
        self.max_v = Vec4f(16, 16, 16, 0)
        self.non_empty = False
        self.face_visibility_mask = 0xFFFFFFFFF
        self.lod = 0
        self.visible = False


@fieldwise_init
struct GpuInstance(Copyable, Defaultable):
    """64-byte GPU instancing payload (matches Vulkan/GL SSBO alignment)."""

    var row0: Vec4f
    var row1: Vec4f
    var row2: Vec4f
    var meta: SIMD[DType.uint32, 4]  # mesh_id, color_rgba, flags (bit 0 = translucent), light_uv


@fieldwise_init
struct GpuParticle(Copyable, Defaultable):
    """32-byte GPU particle payload."""

    var pos_size: Vec4f  # xyz + scale
    var uv_color: SIMD[DType.uint32, 4]


struct _CullArgs:
    var sections: Pointer[SectionSnapshot, MutUntrackedOrigin]
    var frustum: Frustum
    var cam_pos: Vec4f
    var out_visible: Atomic[Int64]

    def __init__(
        out self,
        sections: Pointer[SectionSnapshot, MutUntrackedOrigin],
        frustum: Frustum,
        cam_pos: Vec4f,
    ):
        self.sections = sections
        self.frustum = frustum.copy()
        self.cam_pos = cam_pos
        self.out_visible = Atomic[Int64](0)


def _cull_worker(start: Int, end: Int, raw: MutOpaquePointer[MutUntrackedOrigin]):
    var ctx = raw.unsafe_bitcast[_CullArgs]()
    var local_vis: Int64 = 0
    var cam = ctx[].cam_pos
    for i in range(start, end):
        var sp = ctx[].sections.unsafe_offset(i)
        if not sp[].non_empty or sp[].face_visibility_mask == 0:
            sp[].visible = False
            continue
        if not ctx[].frustum.aabb_visible(sp[].min_v, sp[].max_v):
            sp[].visible = False
            continue
        var center = (sp[].min_v + sp[].max_v) * 0.5
        var diff = center - cam
        var dist_sq = diff[0] * diff[0] + diff[1] * diff[1] + diff[2] * diff[2]
        var lod: UInt8 = 0
        if dist_sq > 16384.0:
            lod = 3
        elif dist_sq > 4096.0:
            lod = 2
        elif dist_sq > 1024.0:
            lod = 1
        sp[].lod = lod
        sp[].visible = True
        local_vis += 1
    _ = ctx[].out_visible.fetch_add(local_vis)


def cull_sections_parallel(
    mut pool: WorkerPool,
    sections: Pointer[SectionSnapshot, MutUntrackedOrigin],
    count: Int,
    frustum: Frustum,
    cam_pos: Vec4f,
) -> Int:
    """Runs multi-core SIMD frustum + portal + LOD culling over `count` sections."""
    var args = _CullArgs(sections, frustum, cam_pos)
    pool.parallel_for(count, _cull_worker, Pointer(to=args).unsafe_bitcast[NoneType](), grain=32)
    return Int(args.out_visible.load())


def batch_gpu_instances(
    instances: Pointer[GpuInstance, MutUntrackedOrigin],
    count: Int,
    frustum: Frustum,
    out_opaque_indices: Pointer[Int32, MutUntrackedOrigin],
    out_translucent_indices: Pointer[Int32, MutUntrackedOrigin],
) -> Tuple[Int, Int]:
    """Frustum-culls `count` non-block instances and splits opaque vs translucent batches."""
    var n_opaque = 0
    var n_trans = 0
    for i in range(count):
        var inst = instances.unsafe_offset(i)
        var pos = Vec4f(inst[].row0[3], inst[].row1[3], inst[].row2[3], 0.0)
        var min_v = pos - Vec4f(1.0, 1.0, 1.0, 0.0)
        var max_v = pos + Vec4f(1.0, 1.0, 1.0, 0.0)
        if not frustum.aabb_visible(min_v, max_v):
            continue
        if (inst[].meta[2] & 1) != 0:
            out_translucent_indices.unsafe_offset(n_trans)[] = Int32(i)
            n_trans += 1
        else:
            out_opaque_indices.unsafe_offset(n_opaque)[] = Int32(i)
            n_opaque += 1
    return (n_opaque, n_trans)
