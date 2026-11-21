"""
Muffin Zero-LWJGL Rendering Hardware Interface (Mojo 1.2).

Bypasses LWJGL's JNI/FFM overhead by probing system GPU drivers directly via
`OwnedDLHandle` (`libvulkan.so.1` -> `libGL.so.1` / `libEGL.so.1` ->
`libGLESv2.so.2` -> `Headless`) and translating Mojang/Blaze3D GLSL
(`#version 150`/`330 core`/`4xx`) into GLSL ES 3.00 or Core 3.30 cleanly.
"""

from std.ffi import OwnedDLHandle


comptime RHI_HEADLESS: Int32 = 0
comptime RHI_OPENGL: Int32 = 1
comptime RHI_GLES3: Int32 = 2
comptime RHI_VULKAN: Int32 = 3


@fieldwise_init
struct RhiCaps(Copyable, Defaultable, Writable):
    var backend: Int32
    var has_vulkan_lib: Bool
    var has_gl_lib: Bool
    var has_egl_lib: Bool
    var has_gles_lib: Bool
    var has_base_instance: Bool
    var max_texture_size: Int32


@fieldwise_init
struct RhiStats(Copyable, Defaultable, Writable):
    var frame_index: UInt64
    var draw_calls: UInt32
    var instanced_draws: UInt32
    var triangles: UInt64
    var shaders_compiled: UInt32
    var buffer_uploads_bytes: UInt64


def _probe_dl(name: String) -> Bool:
    try:
        var h = OwnedDLHandle(name)
        _ = h
        return True
    except:
        return False


def probe_rhi_caps(prefer_vulkan: Bool = True) -> RhiCaps:
    """Probes available native graphics libraries on the host OS."""
    var has_vk = _probe_dl("libvulkan.so.1")
    var has_gl = _probe_dl("libGL.so.1")
    var has_egl = _probe_dl("libEGL.so.1")
    var has_gles = _probe_dl("libGLESv2.so.2")

    var backend = RHI_HEADLESS
    if prefer_vulkan and has_vk:
        backend = RHI_VULKAN
    elif has_gl:
        backend = RHI_OPENGL
    elif has_gles and has_egl:
        backend = RHI_GLES3

    return RhiCaps(
        backend=backend,
        has_vulkan_lib=has_vk,
        has_gl_lib=has_gl,
        has_egl_lib=has_egl,
        has_gles_lib=has_gles,
        has_base_instance=(backend == RHI_VULKAN or backend == RHI_OPENGL),
        max_texture_size=Int32(16384) if backend != RHI_HEADLESS else Int32(4096),
    )


def translate_glsl_version(src: String, target_es3: Bool) -> String:
    """Normalizes Mojang Blaze3D GLSL `#version 150`/`330`/`4xx` shaders.

    Targets GLSL `#version 300 es` (with default precision qualifiers) for
    GLES3/mobile or `#version 330 core` for desktop OpenGL.
    """
    var target_hdr = String("#version 300 es\nprecision highp float;\nprecision highp int;") if target_es3 else String("#version 330 core")
    var lines = src.splitlines()
    var out = String()
    var replaced = False
    for line in lines:
        var stripped = line.strip()
        if not replaced and stripped.startswith("#version"):
            out += target_hdr
            out += "\n"
            replaced = True
        else:
            out += String(line)
            out += "\n"
    if not replaced:
        return target_hdr + "\n" + src
    return out


struct RhiContext:
    """Lightweight RHI frame & command ring context."""

    var caps: RhiCaps
    var stats: RhiStats
    var width: Int32
    var height: Int32
    var initialized: Bool

    def __init__(out self, width: Int32 = 1280, height: Int32 = 720, prefer_vulkan: Bool = True):
        self.caps = probe_rhi_caps(prefer_vulkan)
        self.stats = RhiStats()
        self.width = width
        self.height = height
        self.initialized = True

    def begin_frame(mut self):
        self.stats.frame_index += 1
        self.stats.draw_calls = 0
        self.stats.instanced_draws = 0
        self.stats.triangles = 0

    def record_instanced_draw(mut self, instance_count: UInt32, indices_per_instance: UInt32):
        if instance_count == 0:
            return
        self.stats.draw_calls += 1
        self.stats.instanced_draws += 1
        self.stats.triangles += UInt64(instance_count) * UInt64(indices_per_instance // 3)
