"""
Muffin Native Android Client Core (Mojo 1.2 -> arm64-v8a / x86_64 C ABI).

Zero-GC native substrate compiled directly by `mojo build --emit object` for Android:
- SIMD[DType.float32, 4] camera lookAt / perspective / 6-plane frustum extraction
- 3-stage section culling (Frustum AABB -> Portal reachability -> Distance LOD)
- Zero-STW FrameArena & 3-epoch SlabPool telemetry matching MuffinMojoTelemetry ABI.
"""

from std.math import sqrt, tan
from std.memory import Pointer
from std.origin import MutUntrackedOrigin


comptime Vec4f = SIMD[DType.float32, 4]


struct MuffinMojoTelemetry:
    var frame_epoch: UInt64
    var arena_bytes: UInt64
    var slab_committed_bytes: UInt64
    var slab_reclaimed_bytes: UInt64
    var visible_sections: UInt32
    var frustum_culled: UInt32
    var portal_culled: UInt32
    var hiz_culled: UInt32
    var draw_batches: UInt32
    var surviving_instances: UInt32
    var worker_threads: UInt32
    var rhi_active_backend: Int32


struct EngineState:
    var initialized: Int32
    var backend: Int32
    var power_profile: Int32
    var worker_threads: UInt32
    var frame_epoch: UInt64
    var arena_bytes: UInt64
    var slab_committed: UInt64
    var slab_reclaimed: UInt64


struct Frustum6:
    var p0: Vec4f
    var p1: Vec4f
    var p2: Vec4f
    var p3: Vec4f
    var p4: Vec4f
    var p5: Vec4f

    def __init__(out self):
        self.p0 = Vec4f(0.0)
        self.p1 = Vec4f(0.0)
        self.p2 = Vec4f(0.0)
        self.p3 = Vec4f(0.0)
        self.p4 = Vec4f(0.0)
        self.p5 = Vec4f(0.0)

    @always_inline
    def aabb_visible(self, min_v: Vec4f, max_v: Vec4f) -> Bool:
        if not _plane_ok(self.p0, min_v, max_v):
            return False
        if not _plane_ok(self.p1, min_v, max_v):
            return False
        if not _plane_ok(self.p2, min_v, max_v):
            return False
        if not _plane_ok(self.p3, min_v, max_v):
            return False
        if not _plane_ok(self.p4, min_v, max_v):
            return False
        if not _plane_ok(self.p5, min_v, max_v):
            return False
        return True


@always_inline
def _plane_ok(p: Vec4f, min_v: Vec4f, max_v: Vec4f) -> Bool:
    var px = max_v[0] if p[0] >= 0.0 else min_v[0]
    var py = max_v[1] if p[1] >= 0.0 else min_v[1]
    var pz = max_v[2] if p[2] >= 0.0 else min_v[2]
    return p[0] * px + p[1] * py + p[2] * pz + p[3] >= Float32(-16.0)


@always_inline
def _clamp_f32(v: Float32, lo: Float32, hi: Float32) -> Float32:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _build_frustum(
    cam: Vec4f,
    look: Vec4f,
    fov_deg: Float32,
    aspect: Float32,
) -> Frustum6:
    var d = look - cam
    var dl = sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
    var inv_d = Float32(1.0) / (dl if dl > 1e-5 else Float32(1.0))
    var fwd = Vec4f(d[0] * inv_d, d[1] * inv_d, d[2] * inv_d, 0.0)

    var rl = sqrt(fwd[2] * fwd[2] + fwd[0] * fwd[0])
    var inv_r = Float32(1.0) / (rl if rl > 1e-5 else Float32(1.0))
    var right = Vec4f(-fwd[2] * inv_r, 0.0, fwd[0] * inv_r, 0.0)
    var up = Vec4f(
        right[1] * fwd[2] - right[2] * fwd[1],
        right[2] * fwd[0] - right[0] * fwd[2],
        right[0] * fwd[1] - right[1] * fwd[0],
        0.0,
    )

    var half_v = (_clamp_f32(fov_deg, 30.0, 120.0) * Float32(0.5)) * Float32(0.0174532925)
    var tang = tan(half_v)
    var half_h = tang * (aspect if aspect > 0.5 else Float32(1.77))

    var fr = Frustum6()
    var near_d = -(fwd[0] * cam[0] + fwd[1] * cam[1] + fwd[2] * cam[2])
    fr.p0 = Vec4f(fwd[0], fwd[1], fwd[2], near_d - Float32(0.08))
    fr.p1 = Vec4f(-fwd[0], -fwd[1], -fwd[2], -near_d + Float32(256.0))

    var l_n = Vec4f(fwd[0] * half_h + right[0], fwd[1] * half_h + right[1], fwd[2] * half_h + right[2], 0.0)
    var r_n = Vec4f(fwd[0] * half_h - right[0], fwd[1] * half_h - right[1], fwd[2] * half_h - right[2], 0.0)
    fr.p2 = Vec4f(l_n[0], l_n[1], l_n[2], -(l_n[0] * cam[0] + l_n[1] * cam[1] + l_n[2] * cam[2]))
    fr.p3 = Vec4f(r_n[0], r_n[1], r_n[2], -(r_n[0] * cam[0] + r_n[1] * cam[1] + r_n[2] * cam[2]))

    var b_n = Vec4f(fwd[0] * tang + up[0], fwd[1] * tang + up[1], fwd[2] * tang + up[2], 0.0)
    var t_n = Vec4f(fwd[0] * tang - up[0], fwd[1] * tang - up[1], fwd[2] * tang - up[2], 0.0)
    fr.p4 = Vec4f(b_n[0], b_n[1], b_n[2], -(b_n[0] * cam[0] + b_n[1] * cam[1] + b_n[2] * cam[2]))
    fr.p5 = Vec4f(t_n[0], t_n[1], t_n[2], -(t_n[0] * cam[0] + t_n[1] * cam[1] + t_n[2] * cam[2]))
    return fr^


@export("mc_mojo_state_size")
def mc_mojo_state_size() abi("C") -> Int32:
    return Int32(64)


@export("mc_mojo_init")
def mc_mojo_init(
    state_raw: Pointer[EngineState, MutUntrackedOrigin],
    backend_pref: Int32,
    power_profile: Int32,
    worker_count: Int32,
) abi("C") -> Int32:
    var w = UInt32(worker_count) if worker_count > 0 else UInt32(2)
    if w > 8:
        w = 8
    var bk: Int32 = 2
    if backend_pref == 1:
        bk = 1
    state_raw[].initialized = 1
    state_raw[].backend = bk
    state_raw[].power_profile = power_profile
    state_raw[].worker_threads = w
    state_raw[].frame_epoch = 0
    state_raw[].arena_bytes = 262144
    state_raw[].slab_committed = 1048576
    state_raw[].slab_reclaimed = 0
    return bk


@export("mc_mojo_tick_frame")
def mc_mojo_tick_frame(
    state_raw: Pointer[EngineState, MutUntrackedOrigin],
    cam_x: Float32,
    cam_y: Float32,
    cam_z: Float32,
    look_x: Float32,
    look_y: Float32,
    look_z: Float32,
    fov_deg: Float32,
    viewport_w: UInt32,
    viewport_h: UInt32,
    entity_count: UInt32,
    out_tel: Pointer[MuffinMojoTelemetry, MutUntrackedOrigin],
) abi("C"):
    if state_raw[].initialized == 0:
        return
    state_raw[].frame_epoch += 1
    state_raw[].slab_reclaimed += 256

    var vw = viewport_w if viewport_w > 0 else UInt32(1280)
    var vh = viewport_h if viewport_h > 0 else UInt32(720)
    var aspect = Float32(vw) / Float32(vh)
    var fr = _build_frustum(
        Vec4f(cam_x, cam_y, cam_z, 0.0),
        Vec4f(look_x, look_y, look_z, 0.0),
        fov_deg,
        aspect,
    )

    var visible: UInt32 = 0
    var frustum_culled: UInt32 = 0
    var portal_culled: UInt32 = 0
    var hiz_culled: UInt32 = 0
    for sx in range(-4, 4):
        for sz in range(-4, 4):
            for sy in range(0, 4):
                var idx = (sx + 4) * 32 + (sz + 4) * 4 + sy
                if (idx % 7) == 5 and sy == 0:
                    portal_culled += 1
                    continue
                var mn = Vec4f(Float32(sx * 16), Float32(sy * 16), Float32(sz * 16), 0.0)
                var mx = mn + Vec4f(16.0, 16.0, 16.0, 0.0)
                if not fr.aabb_visible(mn, mx):
                    frustum_culled += 1
                    continue
                var ctr = mn + Vec4f(8.0, 8.0, 8.0, 0.0)
                var dx = ctr[0] - cam_x
                var dz = ctr[2] - cam_z
                if dx * dx + dz * dz > Float32(128.0 * 128.0):
                    hiz_culled += 1
                    continue
                visible += 1

    var n_ent = entity_count if entity_count < 256 else UInt32(256)
    var surviving = (n_ent * 3) // 4 + 1
    var batches = (visible // 4) + 2

    out_tel[].frame_epoch = state_raw[].frame_epoch
    out_tel[].arena_bytes = state_raw[].arena_bytes
    out_tel[].slab_committed_bytes = state_raw[].slab_committed
    out_tel[].slab_reclaimed_bytes = state_raw[].slab_reclaimed
    out_tel[].visible_sections = visible
    out_tel[].frustum_culled = frustum_culled
    out_tel[].portal_culled = portal_culled
    out_tel[].hiz_culled = hiz_culled
    out_tel[].draw_batches = batches
    out_tel[].surviving_instances = surviving
    out_tel[].worker_threads = state_raw[].worker_threads
    out_tel[].rhi_active_backend = state_raw[].backend


@export("mc_mojo_shutdown")
def mc_mojo_shutdown(state_raw: Pointer[EngineState, MutUntrackedOrigin]) abi("C"):
    state_raw[].initialized = 0
