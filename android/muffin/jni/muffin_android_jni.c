/*
 * Muffin Native Android Client Engine & GLES3 Chunk Renderer (`libmuffin_core.so`).
 *
 * Linked directly with Mojo 1.2 C-ABI (`muffin_core.mojo`):
 * - Multi-Core chunk terrain generation (`mc_mojo_terrain_height` on worker pool)
 * - Face-culled + 4-corner Voxel Ambient Occlusion chunk VBO builder (1 draw call / chunk)
 * - Mojo 1.2 SIMD 6-plane frustum culler (`mc_mojo_cull_chunks`)
 * - Real downloaded Mojang `client.jar` 256x256 RGBA block texture atlas support
 * - Translucent water + atmospheric distance fog + fly toggle + sound event notifications
 */

#include <jni.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>
#include <time.h>
#include <dlfcn.h>
#include <pthread.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <android/native_window.h>
#include <android/native_window_jni.h>
#include <android/log.h>

#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, "MuffinMojo", __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "MuffinMojo", __VA_ARGS__)

/* ---- Minimal self-contained EGL / GLES3 ABI ---- */
typedef void *EGLDisplay;
typedef void *EGLConfig;
typedef void *EGLSurface;
typedef void *EGLContext;
typedef unsigned int EGLBoolean;
typedef int EGLint;

#define EGL_DEFAULT_DISPLAY ((void *)0)
#define EGL_NO_DISPLAY ((void *)0)
#define EGL_NO_CONTEXT ((void *)0)
#define EGL_NO_SURFACE ((void *)0)
#define EGL_HEIGHT 0x3056
#define EGL_WIDTH 0x3057
#define EGL_NONE 0x3038
#define EGL_SURFACE_TYPE 0x3033
#define EGL_WINDOW_BIT 0x0004
#define EGL_RENDERABLE_TYPE 0x3040
#define EGL_OPENGL_ES3_BIT 0x0040
#define EGL_RED_SIZE 0x3024
#define EGL_GREEN_SIZE 0x3023
#define EGL_BLUE_SIZE 0x3022
#define EGL_ALPHA_SIZE 0x3021
#define EGL_DEPTH_SIZE 0x3025
#define EGL_CONTEXT_CLIENT_VERSION 0x3098

typedef unsigned int GLenum;
typedef int GLint;
typedef unsigned int GLuint;
typedef int GLsizei;
typedef unsigned char GLboolean;
typedef float GLfloat;
typedef unsigned int GLbitfield;
typedef ptrdiff_t GLsizeiptr;

#define GL_COLOR_BUFFER_BIT 0x00004000
#define GL_DEPTH_BUFFER_BIT 0x00000100
#define GL_DEPTH_TEST 0x0B71
#define GL_CULL_FACE 0x0B44
#define GL_BLEND 0x0BE2
#define GL_SRC_ALPHA 0x0302
#define GL_ONE_MINUS_SRC_ALPHA 0x0303
#define GL_BACK 0x0405
#define GL_TRIANGLES 0x0004
#define GL_FLOAT 0x1406
#define GL_FALSE 0
#define GL_TRUE 1
#define GL_ARRAY_BUFFER 0x8892
#define GL_STATIC_DRAW 0x88E4
#define GL_DYNAMIC_DRAW 0x88E8
#define GL_FRAGMENT_SHADER 0x8B30
#define GL_VERTEX_SHADER 0x8B31
#define GL_COMPILE_STATUS 0x8B81
#define GL_LINK_STATUS 0x8B82
#define GL_RENDERER 0x1F01
#define GL_TEXTURE_2D 0x0DE1
#define GL_TEXTURE0 0x84C0
#define GL_RGBA 0x1908
#define GL_UNSIGNED_BYTE 0x1401
#define GL_TEXTURE_MIN_FILTER 0x2801
#define GL_TEXTURE_MAG_FILTER 0x2800
#define GL_NEAREST 0x2600
#define GL_TEXTURE_WRAP_S 0x2802
#define GL_TEXTURE_WRAP_T 0x2803
#define GL_CLAMP_TO_EDGE 0x812F

typedef EGLDisplay (*PFN_eglGetDisplay)(void *);
typedef EGLBoolean (*PFN_eglInitialize)(EGLDisplay, EGLint *, EGLint *);
typedef EGLBoolean (*PFN_eglChooseConfig)(EGLDisplay, const EGLint *, EGLConfig *, EGLint, EGLint *);
typedef EGLSurface (*PFN_eglCreateWindowSurface)(EGLDisplay, EGLConfig, ANativeWindow *, const EGLint *);
typedef EGLContext (*PFN_eglCreateContext)(EGLDisplay, EGLConfig, EGLContext, const EGLint *);
typedef EGLBoolean (*PFN_eglMakeCurrent)(EGLDisplay, EGLSurface, EGLSurface, EGLContext);
typedef EGLBoolean (*PFN_eglSwapBuffers)(EGLDisplay, EGLSurface);
typedef EGLBoolean (*PFN_eglQuerySurface)(EGLDisplay, EGLSurface, EGLint, EGLint *);
typedef EGLBoolean (*PFN_eglSwapInterval)(EGLDisplay, EGLint);
typedef EGLBoolean (*PFN_eglDestroySurface)(EGLDisplay, EGLSurface);
typedef EGLBoolean (*PFN_eglDestroyContext)(EGLDisplay, EGLContext);

typedef void (*PFN_glViewport)(GLint, GLint, GLsizei, GLsizei);
typedef void (*PFN_glClearColor)(GLfloat, GLfloat, GLfloat, GLfloat);
typedef void (*PFN_glClear)(GLbitfield);
typedef void (*PFN_glEnable)(GLenum);
typedef void (*PFN_glDisable)(GLenum);
typedef void (*PFN_glBlendFunc)(GLenum, GLenum);
typedef void (*PFN_glCullFace)(GLenum);
typedef void (*PFN_glDepthMask)(GLboolean);
typedef const unsigned char *(*PFN_glGetString)(GLenum);
typedef GLuint (*PFN_glCreateShader)(GLenum);
typedef void (*PFN_glShaderSource)(GLuint, GLsizei, const char *const *, const GLint *);
typedef void (*PFN_glCompileShader)(GLuint);
typedef void (*PFN_glGetShaderiv)(GLuint, GLenum, GLint *);
typedef void (*PFN_glDeleteShader)(GLuint);
typedef GLuint (*PFN_glCreateProgram)(void);
typedef void (*PFN_glAttachShader)(GLuint, GLuint);
typedef void (*PFN_glLinkProgram)(GLuint);
typedef void (*PFN_glGetProgramiv)(GLuint, GLenum, GLint *);
typedef void (*PFN_glDeleteProgram)(GLuint);
typedef void (*PFN_glUseProgram)(GLuint);
typedef GLint (*PFN_glGetUniformLocation)(GLuint, const char *);
typedef void (*PFN_glUniformMatrix4fv)(GLint, GLsizei, GLboolean, const GLfloat *);
typedef void (*PFN_glUniform3f)(GLint, GLfloat, GLfloat, GLfloat);
typedef void (*PFN_glUniform1f)(GLint, GLfloat);
typedef void (*PFN_glUniform1i)(GLint, GLint);
typedef void (*PFN_glGenVertexArrays)(GLsizei, GLuint *);
typedef void (*PFN_glBindVertexArray)(GLuint);
typedef void (*PFN_glDeleteVertexArrays)(GLsizei, const GLuint *);
typedef void (*PFN_glGenBuffers)(GLsizei, GLuint *);
typedef void (*PFN_glBindBuffer)(GLenum, GLuint);
typedef void (*PFN_glBufferData)(GLenum, GLsizeiptr, const void *, GLenum);
typedef void (*PFN_glDeleteBuffers)(GLsizei, const GLuint *);
typedef void (*PFN_glEnableVertexAttribArray)(GLuint);
typedef void (*PFN_glVertexAttribPointer)(GLuint, GLint, GLenum, GLboolean, GLsizei, const void *);
typedef void (*PFN_glDrawArrays)(GLenum, GLint, GLsizei);
typedef void (*PFN_glGenTextures)(GLsizei, GLuint *);
typedef void (*PFN_glBindTexture)(GLenum, GLuint);
typedef void (*PFN_glActiveTexture)(GLenum);
typedef void (*PFN_glTexParameteri)(GLenum, GLenum, GLint);
typedef void (*PFN_glTexImage2D)(GLenum, GLint, GLint, GLsizei, GLsizei, GLint, GLenum, GLenum, const void *);
typedef void (*PFN_glDeleteTextures)(GLsizei, const GLuint *);

/* ---- Mojo 1.2 Core C-ABI (`muffin_core.mojo`) ---- */
typedef struct {
    int32_t initialized;
    int32_t backend;
    int32_t power_profile;
    uint32_t worker_threads;
    uint64_t frame_epoch;
    uint64_t arena_bytes;
    uint64_t slab_committed;
    uint64_t slab_reclaimed;
    uint64_t _reserved[2];
} MuffinMojoState;

typedef struct {
    uint64_t frame_epoch;
    uint64_t arena_bytes;
    uint64_t slab_committed_bytes;
    uint64_t slab_reclaimed_bytes;
    uint32_t visible_sections;
    uint32_t frustum_culled;
    uint32_t portal_culled;
    uint32_t hiz_culled;
    uint32_t draw_batches;
    uint32_t surviving_instances;
    uint32_t worker_threads;
    int32_t rhi_active_backend;
} MuffinMojoTelemetry;

extern int32_t mc_mojo_terrain_height(int32_t wx, int32_t wz, int32_t spawn_x, int32_t spawn_z);
extern int32_t mc_mojo_cull_chunks(
    int32_t grid_w, int32_t chunk_h,
    float cam_x, float cam_y, float cam_z,
    float look_x, float look_y, float look_z,
    float fov_deg, float aspect, float max_dist_blocks,
    uint8_t *out_vis);
extern int32_t mc_mojo_init(MuffinMojoState *st, int32_t backend_pref, int32_t power_profile, int32_t worker_count);
extern void mc_mojo_tick_frame(
    MuffinMojoState *st,
    uint32_t visible_chunks, uint32_t culled_chunks, uint32_t draw_batches, uint32_t active_faces,
    MuffinMojoTelemetry *out_tel);
extern void mc_mojo_shutdown(MuffinMojoState *st);

/* ---- GLFW key constants & Touch Input State ---- */
#define KEY_SPACE 32
#define KEY_W 87
#define KEY_A 65
#define KEY_S 83
#define KEY_D 68
#define KEY_E 69
#define KEY_Q 81
#define KEY_F 70
#define KEY_T 84
#define KEY_LSHIFT 340
#define KEY_LCTRL 341
#define KEY_ESC 256
#define KEY_F3 292
#define KEY_F5 294
#define KEY_TAB 258

typedef struct {
    int32_t active_keys[64];
    volatile uint8_t key_w, key_a, key_s, key_d;
    volatile uint8_t key_jump, key_sneak, key_sprint, key_lmb, key_rmb;
    volatile uint8_t key_esc, key_e, key_q, key_f, key_t, key_f3, key_f5, key_tab;
    volatile uint8_t req_break, req_place, third_person;
    volatile float move_x, move_y, look_dx, look_dy;
    volatile int32_t hotbar_slot;
    volatile float sensitivity;
    volatile uint8_t invert_y;
    volatile float fov_deg;
    volatile int32_t render_dist, target_fps;
    volatile uint8_t cursor_visible;
    volatile float cursor_x, cursor_y;
} MuffinInputState;

typedef struct {
    int32_t width, height;
    uint8_t key_w, key_a, key_s, key_d, key_jump, key_sneak, key_sprint, key_lmb, key_rmb;
    uint8_t key_esc, key_e, key_q, key_f, key_t, key_f3, key_f5, key_tab;
    uint8_t third_person, cursor_visible, _pad[2];
    int32_t hotbar_slot;
    float look_dx, look_dy, cursor_x, cursor_y;
} MuffinPollSnapshot;

__attribute__((visibility("default"))) MuffinInputState g_tc_input;
typedef int (*PFN_engine_main)(const char *, const char *, const char *, MuffinInputState *);

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_t g_render_thread;
static bool g_thread_running = false;
static volatile bool g_stop_req = false;
static volatile bool g_surface_dirty = false;
static ANativeWindow *g_window = NULL;
static ANativeWindow *g_pending_window = NULL;
static int g_win_w = 1280, g_win_h = 720, g_egl_w = 0, g_egl_h = 0;
static volatile int32_t g_swap_count = 0;
static int g_backend_pref = 0, g_power_profile = 1;
static MuffinMojoState g_mojo_state = {0};
static MuffinMojoTelemetry g_tel = {0};
static float g_fps = 0.0f;
static char g_username[64] = "MojoOyuncu";
static char g_ext_lib_path[512] = "";
static void *g_ext_handle = NULL;
static PFN_engine_main g_ext_entry_fn = NULL;
static bool g_ext_loaded = false, g_ext_launched = false;
static char g_last_log[2048] = "Muffin Mojo 1.2 Chunk Engine hazir.";

/* Texture Atlas pending upload from Java (256x256 RGBA) */
static uint8_t *g_atlas_rgba = NULL;
static int g_atlas_w = 0, g_atlas_h = 0;
static volatile bool g_atlas_dirty = false;

/* Pending sound trigger bitmask: bits 0..7 break block id, bit 16 = place */
static volatile uint32_t g_sound_event = 0;

static void set_log(const char *msg) {
    pthread_mutex_lock(&g_lock);
    snprintf(g_last_log, sizeof(g_last_log), "%s", msg ? msg : "");
    pthread_mutex_unlock(&g_lock);
    LOGI("%s", msg ? msg : "");
}

/* ---- Streaming 13x13 Multi-Core Chunk World (208 x 64 x 208 = 2.77M Blocks) ---- */
#define GRID_W 13
#define CHUNK_SZ 16
#define CHUNK_H 64
#define WX (GRID_W * CHUNK_SZ) /* 208 */
#define WZ (GRID_W * CHUNK_SZ) /* 208 */
#define WY CHUNK_H             /* 64 */
#define NUM_CHUNKS (GRID_W * GRID_W) /* 169 */
#define WATER_LEVEL 14

/* Block IDs:
 * 0:Air, 1:Grass, 2:Dirt, 3:Stone, 4:OakPlanks, 5:OakLog, 6:Leaves,
 * 7:Cobble, 8:MojoDiamond, 9:Glowstone, 10:Sand, 11:Bricks, 12:Glass, 13:Bedrock, 14:Water */
#define BLK_AIR 0
#define BLK_GRASS 1
#define BLK_DIRT 2
#define BLK_STONE 3
#define BLK_PLANKS 4
#define BLK_LOG 5
#define BLK_LEAVES 6
#define BLK_COBBLE 7
#define BLK_DIAMOND 8
#define BLK_GLOW 9
#define BLK_SAND 10
#define BLK_BRICKS 11
#define BLK_GLASS 12
#define BLK_BEDROCK 13
#define BLK_WATER 14

static uint8_t *g_blocks = NULL; /* WX * WY * WZ */
static uint8_t g_chunk_vis[NUM_CHUNKS];

typedef struct {
    GLuint vbo_op, vbo_wa;
    float *cpu_op, *cpu_wa;
    int verts_op, verts_wa;
    int cap_op, cap_wa;
    volatile bool cpu_ready;
    volatile bool gl_dirty;
} ChunkRender;

static ChunkRender g_chunks[NUM_CHUNKS];

static float g_px = 104.5f, g_py = 23.0f, g_pz = 104.5f, g_vy = 0.0f;
static float g_yaw = 0.0f, g_pitch = -0.12f;
static bool g_on_ground = false, g_flying = false;
static uint64_t g_last_jump_ns = 0;
static bool g_prev_jump_down = false;
static int g_hit_bx = -1, g_hit_by = -1, g_hit_bz = -1;
static int g_place_bx = -1, g_place_by = -1, g_place_bz = -1;

static inline uint8_t get_blk(int x, int y, int z) {
    if (x < 0 || x >= WX || z < 0 || z >= WZ || y < 0) return BLK_BEDROCK;
    if (y >= WY) return BLK_AIR;
    return g_blocks[((size_t)z * WX + x) * WY + y];
}

static inline void set_blk(int x, int y, int z, uint8_t id) {
    if (x < 0 || x >= WX || z < 0 || z >= WZ || y < 0 || y >= WY) return;
    g_blocks[((size_t)z * WX + x) * WY + y] = id;
}

static inline bool is_opaque(uint8_t id) {
    return id != BLK_AIR && id != BLK_WATER && id != BLK_GLASS;
}

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

/* Atlas tile lookup per block & face direction (0:+Y, 1:-Y, 2:+X, 3:-X, 4:+Z, 5:-Z) */
static int tile_for_face(uint8_t id, int dir) {
    switch (id) {
        case BLK_GRASS:   return (dir == 0) ? 0 : ((dir == 1) ? 2 : 1);
        case BLK_DIRT:    return 2;
        case BLK_STONE:   return 3;
        case BLK_PLANKS:  return 4;
        case BLK_LOG:     return (dir <= 1) ? 6 : 5;
        case BLK_LEAVES:  return 7;
        case BLK_COBBLE:  return 8;
        case BLK_SAND:    return 9;
        case BLK_DIAMOND: return 10;
        case BLK_GLOW:    return 11;
        case BLK_BRICKS:  return 12;
        case BLK_GLASS:   return 13;
        case BLK_BEDROCK: return 14;
        case BLK_WATER:   return 15;
        default:          return 3;
    }
}

/* Build face-culled + voxel Ambient Occlusion mesh for chunk cidx */
#define VEC_STRIDE 6 /* x, y, z, u, v, light */
static void push_quad(
    float **buf, int *verts, int *cap,
    float v0[3], float v1[3], float v2[3], float v3[3],
    float u0, float v0_uv, float u1, float v1_uv,
    float l0, float l1, float l2, float l3) {
    if (*verts + 6 > *cap) {
        int ncap = (*cap == 0) ? 4096 : (*cap * 2);
        *buf = (float *)realloc(*buf, (size_t)ncap * VEC_STRIDE * sizeof(float));
        *cap = ncap;
    }
    float *p = *buf + (*verts) * VEC_STRIDE;
    /* Tri 1: 0, 1, 2 */
    p[0]=v0[0]; p[1]=v0[1]; p[2]=v0[2]; p[3]=u0; p[4]=v1_uv; p[5]=l0; p += 6;
    p[0]=v1[0]; p[1]=v1[1]; p[2]=v1[2]; p[3]=u1; p[4]=v1_uv; p[5]=l1; p += 6;
    p[0]=v2[0]; p[1]=v2[1]; p[2]=v2[2]; p[3]=u1; p[4]=v0_uv; p[5]=l2; p += 6;
    /* Tri 2: 0, 2, 3 */
    p[0]=v0[0]; p[1]=v0[1]; p[2]=v0[2]; p[3]=u0; p[4]=v1_uv; p[5]=l0; p += 6;
    p[0]=v2[0]; p[1]=v2[1]; p[2]=v2[2]; p[3]=u1; p[4]=v0_uv; p[5]=l2; p += 6;
    p[0]=v3[0]; p[1]=v3[1]; p[2]=v3[2]; p[3]=u0; p[4]=v0_uv; p[5]=l3;
    *verts += 6;
}

static inline float corner_ao(int s1, int s2, int c) {
    if (s1 && s2) return 0.54f;
    return 1.0f - (float)(s1 + s2 + c) * 0.14f;
}

static void build_chunk_mesh(int cx, int cz) {
    int cidx = cz * GRID_W + cx;
    ChunkRender *cr = &g_chunks[cidx];
    cr->verts_op = 0;
    cr->verts_wa = 0;

    int x0 = cx * CHUNK_SZ, z0 = cz * CHUNK_SZ;
    const float inv16 = 1.0f / 16.0f;
    const float eps = 0.0012f; /* prevents UV bleeding at atlas tile boundaries */

    for (int lx = 0; lx < CHUNK_SZ; lx++) {
        int x = x0 + lx;
        for (int lz = 0; lz < CHUNK_SZ; lz++) {
            int z = z0 + lz;
            for (int y = 0; y < WY; y++) {
                uint8_t id = get_blk(x, y, z);
                if (id == BLK_AIR) continue;
                bool is_wat = (id == BLK_WATER);
                float **tbuf = is_wat ? &cr->cpu_wa : &cr->cpu_op;
                int *tverts = is_wat ? &cr->verts_wa : &cr->verts_op;
                int *tcap = is_wat ? &cr->cap_wa : &cr->cap_op;

                static const int dx[6] = {0, 0, 1, -1, 0, 0};
                static const int dy[6] = {1, -1, 0, 0, 0, 0};
                static const int dz[6] = {0, 0, 0, 0, 1, -1};
                static const float dir_shade[6] = {1.00f, 0.54f, 0.86f, 0.80f, 0.76f, 0.72f};

                for (int d = 0; d < 6; d++) {
                    uint8_t nb = get_blk(x + dx[d], y + dy[d], z + dz[d]);
                    if (is_wat) {
                        if (nb != BLK_AIR || d != 0) continue; /* water top surface exposed to air */
                    } else if (id == BLK_GLASS) {
                        if (nb == BLK_GLASS || is_opaque(nb)) continue;
                    } else {
                        if (is_opaque(nb)) continue;
                    }

                    int tile = tile_for_face(id, d);
                    float u0 = tile * inv16 + eps, u1 = (tile + 1) * inv16 - eps;
                    float uv0 = eps, uv1 = inv16 - eps;

                    float fx = (float)x, fy = (float)y, fz = (float)z;
                    float top_h = (is_wat) ? 0.88f : 1.0f;
                    float v0[3], v1[3], v2[3], v3[3];
                    float ao0 = 1, ao1 = 1, ao2 = 1, ao3 = 1;
                    float sh = (id == BLK_GLOW || id == BLK_DIAMOND) ? 1.05f : dir_shade[d];

                    if (d == 0) { /* +Y */
                        v0[0]=fx;   v0[1]=fy+top_h; v0[2]=fz+1; v1[0]=fx+1; v1[1]=fy+top_h; v1[2]=fz+1;
                        v2[0]=fx+1; v2[1]=fy+top_h; v2[2]=fz;   v3[0]=fx;   v3[1]=fy+top_h; v3[2]=fz;
                        if (!is_wat) {
                            int y1 = y + 1;
                            ao0 = corner_ao(is_opaque(get_blk(x-1,y1,z)), is_opaque(get_blk(x,y1,z+1)), is_opaque(get_blk(x-1,y1,z+1)));
                            ao1 = corner_ao(is_opaque(get_blk(x+1,y1,z)), is_opaque(get_blk(x,y1,z+1)), is_opaque(get_blk(x+1,y1,z+1)));
                            ao2 = corner_ao(is_opaque(get_blk(x+1,y1,z)), is_opaque(get_blk(x,y1,z-1)), is_opaque(get_blk(x+1,y1,z-1)));
                            ao3 = corner_ao(is_opaque(get_blk(x-1,y1,z)), is_opaque(get_blk(x,y1,z-1)), is_opaque(get_blk(x-1,y1,z-1)));
                        }
                    } else if (d == 1) { /* -Y */
                        v0[0]=fx;   v0[1]=fy; v0[2]=fz;   v1[0]=fx+1; v1[1]=fy; v1[2]=fz;
                        v2[0]=fx+1; v2[1]=fy; v2[2]=fz+1; v3[0]=fx;   v3[1]=fy; v3[2]=fz+1;
                    } else if (d == 2) { /* +X */
                        v0[0]=fx+1; v0[1]=fy; v0[2]=fz+1; v1[0]=fx+1; v1[1]=fy; v1[2]=fz;
                        v2[0]=fx+1; v2[1]=fy+1; v2[2]=fz; v3[0]=fx+1; v3[1]=fy+1; v3[2]=fz+1;
                        ao2 = corner_ao(is_opaque(get_blk(x+1,y+1,z)), is_opaque(get_blk(x+1,y,z-1)), is_opaque(get_blk(x+1,y+1,z-1)));
                        ao3 = corner_ao(is_opaque(get_blk(x+1,y+1,z)), is_opaque(get_blk(x+1,y,z+1)), is_opaque(get_blk(x+1,y+1,z+1)));
                    } else if (d == 3) { /* -X */
                        v0[0]=fx; v0[1]=fy; v0[2]=fz;   v1[0]=fx; v1[1]=fy; v1[2]=fz+1;
                        v2[0]=fx; v2[1]=fy+1; v2[2]=fz+1; v3[0]=fx; v3[1]=fy+1; v3[2]=fz;
                        ao2 = corner_ao(is_opaque(get_blk(x-1,y+1,z)), is_opaque(get_blk(x-1,y,z+1)), is_opaque(get_blk(x-1,y+1,z+1)));
                        ao3 = corner_ao(is_opaque(get_blk(x-1,y+1,z)), is_opaque(get_blk(x-1,y,z-1)), is_opaque(get_blk(x-1,y+1,z-1)));
                    } else if (d == 4) { /* +Z */
                        v0[0]=fx;   v0[1]=fy; v0[2]=fz+1; v1[0]=fx+1; v1[1]=fy; v1[2]=fz+1;
                        v2[0]=fx+1; v2[1]=fy+1; v2[2]=fz+1; v3[0]=fx; v3[1]=fy+1; v3[2]=fz+1;
                        ao2 = corner_ao(is_opaque(get_blk(x,y+1,z+1)), is_opaque(get_blk(x+1,y,z+1)), is_opaque(get_blk(x+1,y+1,z+1)));
                        ao3 = corner_ao(is_opaque(get_blk(x,y+1,z+1)), is_opaque(get_blk(x-1,y,z+1)), is_opaque(get_blk(x-1,y+1,z+1)));
                    } else { /* -Z */
                        v0[0]=fx+1; v0[1]=fy; v0[2]=fz;   v1[0]=fx; v1[1]=fy; v1[2]=fz;
                        v2[0]=fx;   v2[1]=fy+1; v2[2]=fz; v3[0]=fx+1; v3[1]=fy+1; v3[2]=fz;
                        ao2 = corner_ao(is_opaque(get_blk(x,y+1,z-1)), is_opaque(get_blk(x-1,y,z-1)), is_opaque(get_blk(x-1,y+1,z-1)));
                        ao3 = corner_ao(is_opaque(get_blk(x,y+1,z-1)), is_opaque(get_blk(x+1,y,z-1)), is_opaque(get_blk(x+1,y+1,z-1)));
                    }
                    push_quad(tbuf, tverts, tcap, v0, v1, v2, v3, u0, uv0, u1, uv1, sh*ao0, sh*ao1, sh*ao2, sh*ao3);
                }
            }
        }
    }
    cr->cpu_ready = true;
    cr->gl_dirty = true;
}

static void mark_block_dirty(int bx, int bz) {
    int cx = bx / CHUNK_SZ, cz = bz / CHUNK_SZ;
    if (cx < 0 || cx >= GRID_W || cz < 0 || cz >= GRID_W) return;
    build_chunk_mesh(cx, cz);
    int lx = bx % CHUNK_SZ, lz = bz % CHUNK_SZ;
    if (lx == 0 && cx > 0) build_chunk_mesh(cx - 1, cz);
    if (lx == CHUNK_SZ - 1 && cx < GRID_W - 1) build_chunk_mesh(cx + 1, cz);
    if (lz == 0 && cz > 0) build_chunk_mesh(cx, cz - 1);
    if (lz == CHUNK_SZ - 1 && cz < GRID_W - 1) build_chunk_mesh(cx, cz + 1);
}

/* Multi-core terrain + mesh initialization */
typedef struct { int start_cz, end_cz; } TerrainJob;

static void *terrain_gen_worker(void *arg) {
    TerrainJob *job = (TerrainJob *)arg;
    for (int cz = job->start_cz; cz < job->end_cz; cz++) {
        for (int cx = 0; cx < GRID_W; cx++) {
            int x0 = cx * CHUNK_SZ, z0 = cz * CHUNK_SZ;
            for (int lx = 0; lx < CHUNK_SZ; lx++) {
                int x = x0 + lx;
                for (int lz = 0; lz < CHUNK_SZ; lz++) {
                    int z = z0 + lz;
                    int h = mc_mojo_terrain_height(x, z, 104, 104);
                    for (int y = 0; y < WY; y++) {
                        uint8_t blk = BLK_AIR;
                        if (y == 0) blk = BLK_BEDROCK;
                        else if (y <= h - 4) {
                            int cave = (int)(sinf(x * 0.19f) * cosf(y * 0.27f) * sinf(z * 0.19f) * 10.0f);
                            if (y < 12 && y > 2 && cave > 6) blk = BLK_AIR;
                            else if ((x * 13 + y * 29 + z * 17) % 67 == 0 && y < 14) blk = BLK_DIAMOND;
                            else blk = BLK_STONE;
                        } else if (y < h) {
                            blk = (h <= WATER_LEVEL + 1) ? BLK_SAND : BLK_DIRT;
                        } else if (y == h) {
                            blk = (h <= WATER_LEVEL + 1) ? BLK_SAND : BLK_GRASS;
                        } else if (y <= WATER_LEVEL) {
                            blk = BLK_WATER;
                        }
                        set_blk(x, y, z, blk);
                    }
                    /* Deterministic oak trees on grass above water */
                    if (h > WATER_LEVEL + 2 && abs(x - 104) > 8 && abs(z - 104) > 8) {
                        uint32_t th = (uint32_t)(x * 73856093 ^ z * 19349663);
                        if ((th % 89u) == 0u && lx >= 2 && lx <= 13 && lz >= 2 && lz <= 13 && h + 7 < WY) {
                            int base = h + 1;
                            for (int ty = 0; ty < 4; ty++) set_blk(x, base + ty, z, BLK_LOG);
                            for (int ox = -2; ox <= 2; ox++) {
                                for (int oz = -2; oz <= 2; oz++) {
                                    for (int oy = 2; oy <= 4; oy++) {
                                        if (abs(ox) + abs(oz) + (oy == 4 ? 1 : 0) <= 3 && get_blk(x+ox, base+oy, z+oz) == BLK_AIR) {
                                            set_blk(x+ox, base+oy, z+oz, BLK_LEAVES);
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    return NULL;
}

static void *mesh_build_worker(void *arg) {
    TerrainJob *job = (TerrainJob *)arg;
    for (int cz = job->start_cz; cz < job->end_cz; cz++) {
        for (int cx = 0; cx < GRID_W; cx++) {
            build_chunk_mesh(cx, cz);
        }
    }
    return NULL;
}

static void init_world_multicore(void) {
    size_t total = (size_t)WX * WY * WZ;
    if (!g_blocks) g_blocks = (uint8_t *)calloc(total, 1);
    else memset(g_blocks, 0, total);
    memset(g_chunks, 0, sizeof(g_chunks));

    pthread_t wt[4];
    TerrainJob jobs[4];
    int step = (GRID_W + 3) / 4;
    int launched = 0;
    for (int t = 0; t < 4; t++) {
        jobs[t].start_cz = t * step;
        jobs[t].end_cz = (t == 3) ? GRID_W : ((t + 1) * step);
        if (jobs[t].start_cz < GRID_W) {
            pthread_create(&wt[launched++], NULL, terrain_gen_worker, &jobs[t]);
        }
    }
    for (int i = 0; i < launched; i++) pthread_join(wt[i], NULL);

    /* Mojo Edition Cyan Crystal Arch & Beacon Shrine at (104, 20, 96) */
    for (int x = 100; x <= 108; x++)
        for (int z = 94; z <= 102; z++) set_blk(x, 19, z, BLK_COBBLE);
    for (int y = 20; y <= 25; y++) {
        set_blk(102, y, 96, BLK_BRICKS);
        set_blk(106, y, 96, BLK_BRICKS);
    }
    for (int x = 102; x <= 106; x++) set_blk(x, 25, 96, BLK_PLANKS);
    set_blk(104, 26, 96, BLK_DIAMOND);
    set_blk(103, 20, 96, BLK_GLOW);
    set_blk(105, 20, 96, BLK_GLOW);
    set_blk(104, 20, 98, BLK_GLASS);
    set_blk(104, 21, 98, BLK_DIAMOND);

    launched = 0;
    for (int t = 0; t < 4; t++) {
        if (jobs[t].start_cz < GRID_W) {
            pthread_create(&wt[launched++], NULL, mesh_build_worker, &jobs[t]);
        }
    }
    for (int i = 0; i < launched; i++) pthread_join(wt[i], NULL);

    g_px = 104.5f; g_py = 21.0f; g_pz = 104.5f;
    g_vy = 0.0f; g_yaw = 0.0f; g_pitch = -0.08f; g_flying = false;
}

static bool is_solid_at(float x, float y, float z) {
    uint8_t b = get_blk((int)floorf(x), (int)floorf(y), (int)floorf(z));
    return b != BLK_AIR && b != BLK_WATER;
}

static bool player_collides(float x, float y, float z) {
    const float r = 0.28f, h = 1.65f;
    return is_solid_at(x - r, y, z - r) || is_solid_at(x + r, y, z - r) ||
           is_solid_at(x - r, y, z + r) || is_solid_at(x + r, y, z + r) ||
           is_solid_at(x - r, y + 0.9f, z - r) || is_solid_at(x + r, y + 0.9f, z + r) ||
           is_solid_at(x - r, y + h, z - r) || is_solid_at(x + r, y + h, z + r);
}

static uint8_t block_for_slot(int slot) {
    static const uint8_t map[9] = {
        BLK_GRASS, BLK_DIRT, BLK_STONE, BLK_PLANKS, BLK_LOG,
        BLK_LEAVES, BLK_COBBLE, BLK_DIAMOND, BLK_GLOW
    };
    return (slot >= 0 && slot < 9) ? map[slot] : BLK_GRASS;
}

static void step_player(float dt) {
    if (dt > 0.05f) dt = 0.05f;
    float sens = g_tc_input.sensitivity > 0.05f ? g_tc_input.sensitivity : 1.0f;
    float dx = g_tc_input.look_dx, dy = g_tc_input.look_dy;
    g_tc_input.look_dx = 0.0f; g_tc_input.look_dy = 0.0f;

    g_yaw += dx * 0.0042f * sens;
    g_pitch += (g_tc_input.invert_y ? dy : -dy) * 0.0042f * sens;
    if (g_pitch > 1.48f) g_pitch = 1.48f;
    if (g_pitch < -1.48f) g_pitch = -1.48f;

    /* Double-tap Jump toggles Creative Flight */
    bool j_now = (g_tc_input.key_jump != 0);
    if (j_now && !g_prev_jump_down) {
        uint64_t t = now_ns();
        if (t - g_last_jump_ns < 280000000ULL) {
            g_flying = !g_flying;
            g_vy = 0.0f;
            g_last_jump_ns = 0;
        } else {
            g_last_jump_ns = t;
        }
    }
    g_prev_jump_down = j_now;

    float fwd = g_tc_input.move_y + (g_tc_input.key_w ? 1.0f : 0.0f) - (g_tc_input.key_s ? 1.0f : 0.0f);
    float strafe = g_tc_input.move_x + (g_tc_input.key_d ? 1.0f : 0.0f) - (g_tc_input.key_a ? 1.0f : 0.0f);
    float len = sqrtf(fwd * fwd + strafe * strafe);
    if (len > 1.0f) { fwd /= len; strafe /= len; }

    float speed = g_flying ? 11.5f : 4.8f;
    if (g_tc_input.key_sprint && fwd > 0.1f && !g_tc_input.key_sneak) speed *= 1.55f;
    if (g_tc_input.key_sneak && !g_flying) speed = 2.0f;

    float sx = sinf(g_yaw), cx = cosf(g_yaw);
    float vx = (sx * fwd + cx * strafe) * speed * dt;
    float vz = (-cx * fwd + sx * strafe) * speed * dt;

    if (!player_collides(g_px + vx, g_py, g_pz)) g_px += vx;
    if (!player_collides(g_px, g_py, g_pz + vz)) g_pz += vz;

    bool in_water = (get_blk((int)floorf(g_px), (int)floorf(g_py + 0.5f), (int)floorf(g_pz)) == BLK_WATER);
    if (g_flying) {
        float fly_vy = (j_now ? 7.5f : 0.0f) - (g_tc_input.key_sneak ? 7.5f : 0.0f);
        float ny = g_py + fly_vy * dt;
        if (ny > 1.5f && ny < (float)(WY - 2) && !player_collides(g_px, ny, g_pz)) g_py = ny;
    } else if (in_water) {
        if (j_now) g_vy = 3.8f;
        else g_vy = fmaxf(-2.5f, g_vy - 6.0f * dt);
        float ny = g_py + g_vy * dt;
        if (!player_collides(g_px, ny, g_pz)) g_py = ny; else g_vy = 0.0f;
    } else {
        if (j_now && g_on_ground) { g_vy = 7.8f; g_on_ground = false; }
        g_vy -= 21.5f * dt;
        float ny = g_py + g_vy * dt;
        if (!player_collides(g_px, ny, g_pz)) { g_py = ny; g_on_ground = false; }
        else { if (g_vy < 0.0f) g_on_ground = true; g_vy = 0.0f; }
    }

    float eye_y = g_py + (g_tc_input.key_sneak ? 1.28f : 1.54f);
    float lx = sinf(g_yaw) * cosf(g_pitch), ly = sinf(g_pitch), lz = -cosf(g_yaw) * cosf(g_pitch);
    g_hit_bx = -1; g_place_bx = -1;
    int px_prev = (int)floorf(g_px), py_prev = (int)floorf(eye_y), pz_prev = (int)floorf(g_pz);
    for (float t = 0.2f; t <= 6.2f; t += 0.06f) {
        int bx = (int)floorf(g_px + lx * t), by = (int)floorf(eye_y + ly * t), bz = (int)floorf(g_pz + lz * t);
        uint8_t b = get_blk(bx, by, bz);
        if (bx >= 0 && bx < WX && by >= 0 && by < WY && bz >= 0 && bz < WZ && b != BLK_AIR && b != BLK_WATER) {
            g_hit_bx = bx; g_hit_by = by; g_hit_bz = bz;
            g_place_bx = px_prev; g_place_by = py_prev; g_place_bz = pz_prev;
            break;
        }
        px_prev = bx; py_prev = by; pz_prev = bz;
    }
    if (g_tc_input.req_break) {
        g_tc_input.req_break = 0;
        if (g_hit_bx >= 0 && g_hit_by > 0) {
            uint8_t broken = get_blk(g_hit_bx, g_hit_by, g_hit_bz);
            set_blk(g_hit_bx, g_hit_by, g_hit_bz, BLK_AIR);
            mark_block_dirty(g_hit_bx, g_hit_bz);
            __atomic_store_n(&g_sound_event, (uint32_t)broken, __ATOMIC_RELAXED);
        }
    }
    if (g_tc_input.req_place) {
        g_tc_input.req_place = 0;
        if (g_place_bx >= 0 && g_place_bx < WX && g_place_by > 0 && g_place_by < WY && g_place_bz >= 0 && g_place_bz < WZ) {
            uint8_t cur = get_blk(g_place_bx, g_place_by, g_place_bz);
            if (cur == BLK_AIR || cur == BLK_WATER) {
                set_blk(g_place_bx, g_place_by, g_place_bz, block_for_slot(g_tc_input.hotbar_slot));
                if (player_collides(g_px, g_py, g_pz)) set_blk(g_place_bx, g_place_by, g_place_bz, cur);
                else {
                    mark_block_dirty(g_place_bx, g_place_bz);
                    __atomic_store_n(&g_sound_event, 0x10000u, __ATOMIC_RELAXED);
                }
            }
        }
    }
}

/* ---- GLES 3.0 Textured + Fog + AO Renderer ---- */
typedef struct {
    void *lib_egl, *lib_gles;
    PFN_eglGetDisplay eglGetDisplay; PFN_eglInitialize eglInitialize;
    PFN_eglChooseConfig eglChooseConfig; PFN_eglCreateWindowSurface eglCreateWindowSurface;
    PFN_eglCreateContext eglCreateContext; PFN_eglMakeCurrent eglMakeCurrent;
    PFN_eglSwapBuffers eglSwapBuffers; PFN_eglQuerySurface eglQuerySurface;
    PFN_eglSwapInterval eglSwapInterval; PFN_eglDestroySurface eglDestroySurface;
    PFN_eglDestroyContext eglDestroyContext;

    PFN_glViewport glViewport; PFN_glClearColor glClearColor; PFN_glClear glClear;
    PFN_glEnable glEnable; PFN_glDisable glDisable; PFN_glBlendFunc glBlendFunc;
    PFN_glCullFace glCullFace; PFN_glDepthMask glDepthMask; PFN_glGetString glGetString;
    PFN_glCreateShader glCreateShader; PFN_glShaderSource glShaderSource;
    PFN_glCompileShader glCompileShader; PFN_glGetShaderiv glGetShaderiv;
    PFN_glDeleteShader glDeleteShader; PFN_glCreateProgram glCreateProgram;
    PFN_glAttachShader glAttachShader; PFN_glLinkProgram glLinkProgram;
    PFN_glGetProgramiv glGetProgramiv; PFN_glDeleteProgram glDeleteProgram;
    PFN_glUseProgram glUseProgram; PFN_glGetUniformLocation glGetUniformLocation;
    PFN_glUniformMatrix4fv glUniformMatrix4fv; PFN_glUniform3f glUniform3f;
    PFN_glUniform1f glUniform1f; PFN_glUniform1i glUniform1i;
    PFN_glGenVertexArrays glGenVertexArrays; PFN_glBindVertexArray glBindVertexArray;
    PFN_glDeleteVertexArrays glDeleteVertexArrays; PFN_glGenBuffers glGenBuffers;
    PFN_glBindBuffer glBindBuffer; PFN_glBufferData glBufferData;
    PFN_glDeleteBuffers glDeleteBuffers; PFN_glEnableVertexAttribArray glEnableVertexAttribArray;
    PFN_glVertexAttribPointer glVertexAttribPointer; PFN_glDrawArrays glDrawArrays;
    PFN_glGenTextures glGenTextures; PFN_glBindTexture glBindTexture;
    PFN_glActiveTexture glActiveTexture; PFN_glTexParameteri glTexParameteri;
    PFN_glTexImage2D glTexImage2D; PFN_glDeleteTextures glDeleteTextures;

    EGLDisplay dpy; EGLConfig cfg; EGLSurface surf; EGLContext ctx;
    GLuint prog, vao, box_prog, box_vao, box_vbo, tex_atlas;
    GLint u_mvp, u_cam, u_fog_far, u_atlas, u_alpha_mul;
    GLint bu_mvp, bu_offset;
    bool gl_ready;
} GlState;

static GlState g_ext_gl = {0};

static const char *VS_CHUNK =
    "#version 300 es\n"
    "layout(location=0) in vec3 aPos;\n"
    "layout(location=1) in vec2 aUv;\n"
    "layout(location=2) in float aLight;\n"
    "uniform mat4 uMvp;\n"
    "uniform vec3 uCam;\n"
    "out vec2 vUv;\n"
    "out float vLight;\n"
    "out float vDist;\n"
    "void main() {\n"
    "  gl_Position = uMvp * vec4(aPos, 1.0);\n"
    "  vUv = aUv;\n"
    "  vLight = aLight;\n"
    "  vDist = length(aPos - uCam);\n"
    "}\n";

static const char *FS_CHUNK =
    "#version 300 es\n"
    "precision mediump float;\n"
    "uniform sampler2D uAtlas;\n"
    "uniform float uFogFar;\n"
    "uniform float uAlphaMul;\n"
    "in vec2 vUv;\n"
    "in float vLight;\n"
    "in float vDist;\n"
    "out vec4 oColor;\n"
    "void main() {\n"
    "  vec4 t = texture(uAtlas, vUv);\n"
    "  if (t.a < 0.08) discard;\n"
    "  vec3 lit = t.rgb * vLight;\n"
    "  float fog = clamp((vDist - uFogFar * 0.45) / max(12.0, uFogFar * 0.55), 0.0, 1.0);\n"
    "  vec3 col = mix(lit, vec3(0.50, 0.76, 0.98), fog * fog);\n"
    "  oColor = vec4(col, t.a * uAlphaMul);\n"
    "}\n";

static const char *VS_BOX =
    "#version 300 es\n"
    "layout(location=0) in vec3 aPos;\n"
    "uniform mat4 uMvp;\n"
    "uniform vec3 uOffset;\n"
    "void main() { gl_Position = uMvp * vec4(aPos * 1.006 + uOffset - 0.003, 1.0); }\n";

static const char *FS_BOX =
    "#version 300 es\n"
    "precision mediump float;\n"
    "out vec4 oColor;\n"
    "void main() { oColor = vec4(0.95, 0.98, 1.0, 0.28); }\n";

static const float UNIT_BOX[36 * 3] = {
    0,0,1, 1,0,1, 1,1,1, 0,0,1, 1,1,1, 0,1,1,
    1,0,0, 0,0,0, 0,1,0, 1,0,0, 0,1,0, 1,1,0,
    1,0,1, 1,0,0, 1,1,0, 1,0,1, 1,1,0, 1,1,1,
    0,0,0, 0,0,1, 0,1,1, 0,0,0, 0,1,1, 0,1,0,
    0,1,1, 1,1,1, 1,1,0, 0,1,1, 1,1,0, 0,1,0,
    0,0,0, 1,0,0, 1,0,1, 0,0,0, 1,0,1, 0,0,1
};

static bool load_gl_procs(GlState *g) {
    if (!g->lib_egl) g->lib_egl = dlopen("libEGL.so", RTLD_NOW);
    if (!g->lib_gles) g->lib_gles = dlopen("libGLESv3.so", RTLD_NOW);
    if (!g->lib_egl || !g->lib_gles) return false;
    g->eglGetDisplay = (PFN_eglGetDisplay)dlsym(g->lib_egl, "eglGetDisplay");
    g->eglInitialize = (PFN_eglInitialize)dlsym(g->lib_egl, "eglInitialize");
    g->eglChooseConfig = (PFN_eglChooseConfig)dlsym(g->lib_egl, "eglChooseConfig");
    g->eglCreateWindowSurface = (PFN_eglCreateWindowSurface)dlsym(g->lib_egl, "eglCreateWindowSurface");
    g->eglCreateContext = (PFN_eglCreateContext)dlsym(g->lib_egl, "eglCreateContext");
    g->eglMakeCurrent = (PFN_eglMakeCurrent)dlsym(g->lib_egl, "eglMakeCurrent");
    g->eglSwapBuffers = (PFN_eglSwapBuffers)dlsym(g->lib_egl, "eglSwapBuffers");
    g->eglQuerySurface = (PFN_eglQuerySurface)dlsym(g->lib_egl, "eglQuerySurface");
    g->eglSwapInterval = (PFN_eglSwapInterval)dlsym(g->lib_egl, "eglSwapInterval");
    g->eglDestroySurface = (PFN_eglDestroySurface)dlsym(g->lib_egl, "eglDestroySurface");
    g->eglDestroyContext = (PFN_eglDestroyContext)dlsym(g->lib_egl, "eglDestroyContext");

    g->glViewport = (PFN_glViewport)dlsym(g->lib_gles, "glViewport");
    g->glClearColor = (PFN_glClearColor)dlsym(g->lib_gles, "glClearColor");
    g->glClear = (PFN_glClear)dlsym(g->lib_gles, "glClear");
    g->glEnable = (PFN_glEnable)dlsym(g->lib_gles, "glEnable");
    g->glDisable = (PFN_glDisable)dlsym(g->lib_gles, "glDisable");
    g->glBlendFunc = (PFN_glBlendFunc)dlsym(g->lib_gles, "glBlendFunc");
    g->glCullFace = (PFN_glCullFace)dlsym(g->lib_gles, "glCullFace");
    g->glDepthMask = (PFN_glDepthMask)dlsym(g->lib_gles, "glDepthMask");
    g->glGetString = (PFN_glGetString)dlsym(g->lib_gles, "glGetString");
    g->glCreateShader = (PFN_glCreateShader)dlsym(g->lib_gles, "glCreateShader");
    g->glShaderSource = (PFN_glShaderSource)dlsym(g->lib_gles, "glShaderSource");
    g->glCompileShader = (PFN_glCompileShader)dlsym(g->lib_gles, "glCompileShader");
    g->glGetShaderiv = (PFN_glGetShaderiv)dlsym(g->lib_gles, "glGetShaderiv");
    g->glDeleteShader = (PFN_glDeleteShader)dlsym(g->lib_gles, "glDeleteShader");
    g->glCreateProgram = (PFN_glCreateProgram)dlsym(g->lib_gles, "glCreateProgram");
    g->glAttachShader = (PFN_glAttachShader)dlsym(g->lib_gles, "glAttachShader");
    g->glLinkProgram = (PFN_glLinkProgram)dlsym(g->lib_gles, "glLinkProgram");
    g->glGetProgramiv = (PFN_glGetProgramiv)dlsym(g->lib_gles, "glGetProgramiv");
    g->glDeleteProgram = (PFN_glDeleteProgram)dlsym(g->lib_gles, "glDeleteProgram");
    g->glUseProgram = (PFN_glUseProgram)dlsym(g->lib_gles, "glUseProgram");
    g->glGetUniformLocation = (PFN_glGetUniformLocation)dlsym(g->lib_gles, "glGetUniformLocation");
    g->glUniformMatrix4fv = (PFN_glUniformMatrix4fv)dlsym(g->lib_gles, "glUniformMatrix4fv");
    g->glUniform3f = (PFN_glUniform3f)dlsym(g->lib_gles, "glUniform3f");
    g->glUniform1f = (PFN_glUniform1f)dlsym(g->lib_gles, "glUniform1f");
    g->glUniform1i = (PFN_glUniform1i)dlsym(g->lib_gles, "glUniform1i");
    g->glGenVertexArrays = (PFN_glGenVertexArrays)dlsym(g->lib_gles, "glGenVertexArrays");
    g->glBindVertexArray = (PFN_glBindVertexArray)dlsym(g->lib_gles, "glBindVertexArray");
    g->glDeleteVertexArrays = (PFN_glDeleteVertexArrays)dlsym(g->lib_gles, "glDeleteVertexArrays");
    g->glGenBuffers = (PFN_glGenBuffers)dlsym(g->lib_gles, "glGenBuffers");
    g->glBindBuffer = (PFN_glBindBuffer)dlsym(g->lib_gles, "glBindBuffer");
    g->glBufferData = (PFN_glBufferData)dlsym(g->lib_gles, "glBufferData");
    g->glDeleteBuffers = (PFN_glDeleteBuffers)dlsym(g->lib_gles, "glDeleteBuffers");
    g->glEnableVertexAttribArray = (PFN_glEnableVertexAttribArray)dlsym(g->lib_gles, "glEnableVertexAttribArray");
    g->glVertexAttribPointer = (PFN_glVertexAttribPointer)dlsym(g->lib_gles, "glVertexAttribPointer");
    g->glDrawArrays = (PFN_glDrawArrays)dlsym(g->lib_gles, "glDrawArrays");
    g->glGenTextures = (PFN_glGenTextures)dlsym(g->lib_gles, "glGenTextures");
    g->glBindTexture = (PFN_glBindTexture)dlsym(g->lib_gles, "glBindTexture");
    g->glActiveTexture = (PFN_glActiveTexture)dlsym(g->lib_gles, "glActiveTexture");
    g->glTexParameteri = (PFN_glTexParameteri)dlsym(g->lib_gles, "glTexParameteri");
    g->glTexImage2D = (PFN_glTexImage2D)dlsym(g->lib_gles, "glTexImage2D");
    g->glDeleteTextures = (PFN_glDeleteTextures)dlsym(g->lib_gles, "glDeleteTextures");
    return g->eglGetDisplay && g->glDrawArrays && g->glTexImage2D;
}

static GLuint make_prog(GlState *g, const char *vsrc, const char *fsrc) {
    GLuint vs = g->glCreateShader(GL_VERTEX_SHADER);
    g->glShaderSource(vs, 1, &vsrc, NULL); g->glCompileShader(vs);
    GLuint fs = g->glCreateShader(GL_FRAGMENT_SHADER);
    g->glShaderSource(fs, 1, &fsrc, NULL); g->glCompileShader(fs);
    GLint okv = 0, okf = 0;
    g->glGetShaderiv(vs, GL_COMPILE_STATUS, &okv);
    g->glGetShaderiv(fs, GL_COMPILE_STATUS, &okf);
    if (!okv || !okf) { g->glDeleteShader(vs); g->glDeleteShader(fs); return 0; }
    GLuint p = g->glCreateProgram();
    g->glAttachShader(p, vs); g->glAttachShader(p, fs); g->glLinkProgram(p);
    g->glDeleteShader(vs); g->glDeleteShader(fs);
    GLint okl = 0; g->glGetProgramiv(p, GL_LINK_STATUS, &okl);
    if (!okl) { g->glDeleteProgram(p); return 0; }
    return p;
}

static bool init_egl_surface(GlState *g, ANativeWindow *win) {
    g->gl_ready = false;
    g->dpy = g->eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (g->dpy == EGL_NO_DISPLAY) return false;
    EGLint maj = 0, min = 0;
    if (!g->eglInitialize(g->dpy, &maj, &min)) return false;
    const EGLint cfg24[] = {
        EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_DEPTH_SIZE, 24, EGL_NONE
    };
    const EGLint cfg16[] = {
        EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_DEPTH_SIZE, 16, EGL_NONE
    };
    EGLint num = 0;
    if ((!g->eglChooseConfig(g->dpy, cfg24, &g->cfg, 1, &num) || num < 1) &&
        (!g->eglChooseConfig(g->dpy, cfg16, &g->cfg, 1, &num) || num < 1)) return false;
    g->surf = g->eglCreateWindowSurface(g->dpy, g->cfg, win, NULL);
    if (g->surf == EGL_NO_SURFACE) return false;
    const EGLint ctx_attrs[] = { EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE };
    g->ctx = g->eglCreateContext(g->dpy, g->cfg, EGL_NO_CONTEXT, ctx_attrs);
    if (g->ctx == EGL_NO_CONTEXT) return false;
    if (!g->eglMakeCurrent(g->dpy, g->surf, g->surf, g->ctx)) return false;
    if (g->eglSwapInterval) g->eglSwapInterval(g->dpy, g_power_profile == 0 ? 0 : 1);

    g->prog = make_prog(g, VS_CHUNK, FS_CHUNK);
    g->box_prog = make_prog(g, VS_BOX, FS_BOX);
    if (!g->prog || !g->box_prog) return false;

    g->u_mvp = g->glGetUniformLocation(g->prog, "uMvp");
    g->u_cam = g->glGetUniformLocation(g->prog, "uCam");
    g->u_fog_far = g->glGetUniformLocation(g->prog, "uFogFar");
    g->u_atlas = g->glGetUniformLocation(g->prog, "uAtlas");
    g->u_alpha_mul = g->glGetUniformLocation(g->prog, "uAlphaMul");

    g->bu_mvp = g->glGetUniformLocation(g->box_prog, "uMvp");
    g->bu_offset = g->glGetUniformLocation(g->box_prog, "uOffset");

    g->glGenVertexArrays(1, &g->vao);
    g->glGenVertexArrays(1, &g->box_vao);
    g->glBindVertexArray(g->box_vao);
    g->glGenBuffers(1, &g->box_vbo);
    g->glBindBuffer(GL_ARRAY_BUFFER, g->box_vbo);
    g->glBufferData(GL_ARRAY_BUFFER, sizeof(UNIT_BOX), UNIT_BOX, GL_STATIC_DRAW);
    g->glEnableVertexAttribArray(0);
    g->glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 3 * sizeof(float), (void *)0);

    g->glGenTextures(1, &g->tex_atlas);
    g->glActiveTexture(GL_TEXTURE0);
    g->glBindTexture(GL_TEXTURE_2D, g->tex_atlas);
    g->glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    g->glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    g->glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    g->glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);

    for (int i = 0; i < NUM_CHUNKS; i++) {
        g_chunks[i].vbo_op = 0; g_chunks[i].vbo_wa = 0;
        if (g_chunks[i].cpu_ready) g_chunks[i].gl_dirty = true;
    }

    g->glEnable(GL_DEPTH_TEST);
    g->glEnable(GL_CULL_FACE);
    g->glCullFace(GL_BACK);
    g->gl_ready = true;
    g_atlas_dirty = true;
    return true;
}

static void destroy_egl(GlState *g) {
    g->gl_ready = false;
    if (g->dpy != EGL_NO_DISPLAY) {
        if (g->eglMakeCurrent) g->eglMakeCurrent(g->dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        for (int i = 0; i < NUM_CHUNKS; i++) {
            if (g_chunks[i].vbo_op && g->glDeleteBuffers) g->glDeleteBuffers(1, &g_chunks[i].vbo_op);
            if (g_chunks[i].vbo_wa && g->glDeleteBuffers) g->glDeleteBuffers(1, &g_chunks[i].vbo_wa);
            g_chunks[i].vbo_op = 0; g_chunks[i].vbo_wa = 0;
        }
        if (g->tex_atlas && g->glDeleteTextures) g->glDeleteTextures(1, &g->tex_atlas);
        if (g->prog && g->glDeleteProgram) g->glDeleteProgram(g->prog);
        if (g->box_prog && g->glDeleteProgram) g->glDeleteProgram(g->box_prog);
        if (g->box_vbo && g->glDeleteBuffers) g->glDeleteBuffers(1, &g->box_vbo);
        if (g->vao && g->glDeleteVertexArrays) g->glDeleteVertexArrays(1, &g->vao);
        if (g->box_vao && g->glDeleteVertexArrays) g->glDeleteVertexArrays(1, &g->box_vao);
        if (g->surf != EGL_NO_SURFACE && g->eglDestroySurface) g->eglDestroySurface(g->dpy, g->surf);
        if (g->ctx != EGL_NO_CONTEXT && g->eglDestroyContext) g->eglDestroyContext(g->dpy, g->ctx);
    }
    void *le = g->lib_egl, *lg = g->lib_gles;
    memset(g, 0, sizeof(*g));
    g->lib_egl = le; g->lib_gles = lg;
}

static void build_mvp(float *out16, float aspect, float fov_deg,
                      float ex, float ey, float ez, float fx, float fy, float fz) {
    float f = 1.0f / tanf((fov_deg * 0.5f) * 3.14159265f / 180.0f);
    float nearp = 0.08f, farp = 280.0f, nf = 1.0f / (nearp - farp);
    float p[16] = {
        f / aspect, 0, 0, 0,  0, f, 0, 0,
        0, 0, (farp + nearp) * nf, -1.0f,  0, 0, 2.0f * farp * nearp * nf, 0
    };
    float rx = -fz, ry = 0.0f, rz = fx, rlen = sqrtf(rx * rx + rz * rz);
    if (rlen > 1e-6f) { rx /= rlen; rz /= rlen; } else { rx = 1.0f; rz = 0.0f; }
    float ux = ry * fz - rz * fy, uy = rz * fx - rx * fz, uz = rx * fy - ry * fx;
    float v[16] = {
        rx, ux, -fx, 0,  ry, uy, -fy, 0,  rz, uz, -fz, 0,
        -(rx * ex + ry * ey + rz * ez), -(ux * ex + uy * ey + uz * ez), (fx * ex + fy * ey + fz * ez), 1.0f
    };
    for (int col = 0; col < 4; col++)
        for (int row = 0; row < 4; row++) {
            float sum = 0.0f;
            for (int k = 0; k < 4; k++) sum += p[k * 4 + row] * v[col * 4 + k];
            out16[col * 4 + row] = sum;
        }
}

static void upload_atlas_if_dirty(GlState *g) {
    if (!g_atlas_dirty) return;
    pthread_mutex_lock(&g_lock);
    bool have = (g_atlas_rgba != NULL && g_atlas_w > 0 && g_atlas_h > 0);
    if (have) {
        g->glActiveTexture(GL_TEXTURE0);
        g->glBindTexture(GL_TEXTURE_2D, g->tex_atlas);
        g->glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, g_atlas_w, g_atlas_h, 0, GL_RGBA, GL_UNSIGNED_BYTE, g_atlas_rgba);
        g_atlas_dirty = false;
    }
    pthread_mutex_unlock(&g_lock);
}

static void *render_loop(void *arg) {
    (void)arg;
    GlState gl = {0};
    if (!load_gl_procs(&gl)) { set_log("HATA: EGL/GLES3 yuklenemedi"); return NULL; }
    int32_t bk = mc_mojo_init(&g_mojo_state, g_backend_pref, g_power_profile, 4);
    set_log("Mojo 1.2 Multi-Core Chunk Voxel Engine aktif (2.77M blok, AO + Face Cull + client.jar Atlas).");

    uint64_t prev_ns = now_ns(), fps_acc_ns = 0;
    uint32_t fps_frames = 0;

    while (!g_stop_req) {
        pthread_mutex_lock(&g_lock);
        if (g_surface_dirty) {
            g_surface_dirty = false;
            if (g_window) { destroy_egl(&gl); load_gl_procs(&gl); ANativeWindow_release(g_window); g_window = NULL; }
            g_window = g_pending_window; g_pending_window = NULL;
            if (g_window && !init_egl_surface(&gl, g_window)) {
                destroy_egl(&gl); load_gl_procs(&gl); set_log("HATA: EGL surface baslatilamadi");
            }
        }
        int w = g_win_w > 0 ? g_win_w : 1280, h = g_win_h > 0 ? g_win_h : 720;
        ANativeWindow *cur_win = g_window;
        pthread_mutex_unlock(&g_lock);

        if (!cur_win || !gl.gl_ready) { usleep(16000); prev_ns = now_ns(); continue; }

        upload_atlas_if_dirty(&gl);
        uint64_t cur_ns = now_ns();
        float dt = (float)(cur_ns - prev_ns) / 1e9f;
        prev_ns = cur_ns;
        step_player(dt);

        float eye_y = g_py + (g_tc_input.key_sneak ? 1.28f : 1.54f);
        float fx = sinf(g_yaw) * cosf(g_pitch), fy = sinf(g_pitch), fz = -cosf(g_yaw) * cosf(g_pitch);
        float cam_x = g_px, cam_y = eye_y, cam_z = g_pz;
        if (g_tc_input.third_person) { cam_x -= fx * 3.5f; cam_y -= fy * 3.5f - 0.5f; cam_z -= fz * 3.5f; }
        float fov = g_tc_input.fov_deg > 30.0f ? g_tc_input.fov_deg : 75.0f;
        float aspect = (float)w / (float)h;
        float max_dist = (float)((g_tc_input.render_dist > 2 ? g_tc_input.render_dist : 12) * CHUNK_SZ);

        int32_t vis_chunks = mc_mojo_cull_chunks(
            GRID_W, WY, cam_x, cam_y, cam_z, cam_x + fx * 16.0f, cam_y + fy * 16.0f, cam_z + fz * 16.0f,
            fov, aspect, max_dist, g_chunk_vis);

        gl.glViewport(0, 0, w, h);
        gl.glClearColor(0.50f, 0.76f, 0.98f, 1.0f);
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
        gl.glUseProgram(gl.prog);
        gl.glActiveTexture(GL_TEXTURE0);
        gl.glBindTexture(GL_TEXTURE_2D, gl.tex_atlas);
        gl.glUniform1i(gl.u_atlas, 0);

        float mvp[16];
        build_mvp(mvp, aspect, fov, cam_x, cam_y, cam_z, fx, fy, fz);
        gl.glUniformMatrix4fv(gl.u_mvp, 1, GL_FALSE, mvp);
        gl.glUniform3f(gl.u_cam, cam_x, cam_y, cam_z);
        gl.glUniform1f(gl.u_fog_far, max_dist);
        gl.glUniform1f(gl.u_alpha_mul, 1.0f);
        gl.glBindVertexArray(gl.vao);

        uint32_t drawn_batches = 0, total_faces = 0;
        /* Pass 1: Opaque Chunks (1 GL draw call per chunk!) */
        for (int i = 0; i < NUM_CHUNKS; i++) {
            if (!g_chunk_vis[i]) continue;
            ChunkRender *cr = &g_chunks[i];
            if (cr->gl_dirty && cr->cpu_ready) {
                cr->gl_dirty = false;
                if (!cr->vbo_op) gl.glGenBuffers(1, &cr->vbo_op);
                if (cr->verts_op > 0) {
                    gl.glBindBuffer(GL_ARRAY_BUFFER, cr->vbo_op);
                    gl.glBufferData(GL_ARRAY_BUFFER, (GLsizeiptr)cr->verts_op * VEC_STRIDE * sizeof(float), cr->cpu_op, GL_DYNAMIC_DRAW);
                }
                if (!cr->vbo_wa) gl.glGenBuffers(1, &cr->vbo_wa);
                if (cr->verts_wa > 0) {
                    gl.glBindBuffer(GL_ARRAY_BUFFER, cr->vbo_wa);
                    gl.glBufferData(GL_ARRAY_BUFFER, (GLsizeiptr)cr->verts_wa * VEC_STRIDE * sizeof(float), cr->cpu_wa, GL_DYNAMIC_DRAW);
                }
            }
            if (cr->verts_op > 0 && cr->vbo_op) {
                gl.glBindBuffer(GL_ARRAY_BUFFER, cr->vbo_op);
                gl.glEnableVertexAttribArray(0); gl.glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, VEC_STRIDE * sizeof(float), (void *)0);
                gl.glEnableVertexAttribArray(1); gl.glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, VEC_STRIDE * sizeof(float), (void *)(3 * sizeof(float)));
                gl.glEnableVertexAttribArray(2); gl.glVertexAttribPointer(2, 1, GL_FLOAT, GL_FALSE, VEC_STRIDE * sizeof(float), (void *)(5 * sizeof(float)));
                gl.glDrawArrays(GL_TRIANGLES, 0, cr->verts_op);
                drawn_batches++; total_faces += (uint32_t)(cr->verts_op / 6);
            }
        }

        /* Pass 2: Translucent Water */
        gl.glEnable(GL_BLEND);
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
        gl.glDepthMask(GL_FALSE);
        gl.glUniform1f(gl.u_alpha_mul, 0.78f);
        for (int i = 0; i < NUM_CHUNKS; i++) {
            if (!g_chunk_vis[i]) continue;
            ChunkRender *cr = &g_chunks[i];
            if (cr->verts_wa > 0 && cr->vbo_wa) {
                gl.glBindBuffer(GL_ARRAY_BUFFER, cr->vbo_wa);
                gl.glEnableVertexAttribArray(0); gl.glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, VEC_STRIDE * sizeof(float), (void *)0);
                gl.glEnableVertexAttribArray(1); gl.glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, VEC_STRIDE * sizeof(float), (void *)(3 * sizeof(float)));
                gl.glEnableVertexAttribArray(2); gl.glVertexAttribPointer(2, 1, GL_FLOAT, GL_FALSE, VEC_STRIDE * sizeof(float), (void *)(5 * sizeof(float)));
                gl.glDrawArrays(GL_TRIANGLES, 0, cr->verts_wa);
                drawn_batches++; total_faces += (uint32_t)(cr->verts_wa / 6);
            }
        }
        gl.glDepthMask(GL_TRUE);

        /* Pass 3: Target Block Highlight */
        if (g_hit_bx >= 0) {
            gl.glUseProgram(gl.box_prog);
            gl.glUniformMatrix4fv(gl.bu_mvp, 1, GL_FALSE, mvp);
            gl.glUniform3f(gl.bu_offset, (float)g_hit_bx, (float)g_hit_by, (float)g_hit_bz);
            gl.glBindVertexArray(gl.box_vao);
            gl.glDrawArrays(GL_TRIANGLES, 0, 36);
        }
        gl.glDisable(GL_BLEND);

        gl.eglSwapBuffers(gl.dpy, gl.surf);
        __atomic_add_fetch(&g_swap_count, 1, __ATOMIC_RELAXED);

        MuffinMojoTelemetry tel = {0};
        mc_mojo_tick_frame(&g_mojo_state, (uint32_t)vis_chunks, (uint32_t)(NUM_CHUNKS - vis_chunks), drawn_batches, total_faces, &tel);
        (void)bk;

        fps_frames++;
        uint64_t end_ns = now_ns();
        fps_acc_ns += (end_ns - cur_ns);
        if (fps_frames >= 12) {
            g_fps = (float)fps_frames / fmaxf((float)fps_acc_ns / 1e9f, 0.001f);
            fps_frames = 0; fps_acc_ns = 0;
        }
        pthread_mutex_lock(&g_lock);
        g_tel = tel;
        pthread_mutex_unlock(&g_lock);

        if (g_tc_input.target_fps > 0 && g_tc_input.target_fps < 120) {
            uint64_t budget = 1000000ULL / (uint64_t)g_tc_input.target_fps;
            uint64_t used = (now_ns() - cur_ns) / 1000ULL;
            if (used < budget) usleep((useconds_t)(budget - used));
        }
    }

    pthread_mutex_lock(&g_lock);
    if (g_window) { ANativeWindow_release(g_window); g_window = NULL; }
    if (g_pending_window) { ANativeWindow_release(g_pending_window); g_pending_window = NULL; }
    pthread_mutex_unlock(&g_lock);
    destroy_egl(&gl);
    mc_mojo_shutdown(&g_mojo_state);
    return NULL;
}

/* ---- Crash / stdio handlers ---- */
static int g_stdio_pipe[2] = {-1, -1};
static int g_engine_log_fd = -1;
static char g_crash_path[512];
static struct sigaction g_prev_sa[32];
static pthread_once_t g_stdio_once = PTHREAD_ONCE_INIT;

static void crash_handler(int sig, siginfo_t *si, void *ucv) {
    (void)ucv;
    int fd = g_crash_path[0] ? open(g_crash_path, O_WRONLY | O_CREAT | O_TRUNC, 0644) : -1;
    if (fd >= 0) {
        char buf[160];
        int n = snprintf(buf, sizeof(buf), "signal %d addr %p\n", sig, si ? si->si_addr : NULL);
        if (n > 0) (void)!write(fd, buf, (size_t)n);
        close(fd);
    }
    struct sigaction *prev = (sig > 0 && sig < 32) ? &g_prev_sa[sig] : NULL;
    if (prev && (prev->sa_flags & SA_SIGINFO) && prev->sa_sigaction) prev->sa_sigaction(sig, si, ucv);
    else { signal(sig, SIG_DFL); raise(sig); }
}

static void install_crash_handler(const char *game_dir) {
    snprintf(g_crash_path, sizeof(g_crash_path), "%s/native_crash.txt", game_dir);
    char logp[512]; snprintf(logp, sizeof(logp), "%s/engine_log.txt", game_dir);
    if (g_engine_log_fd < 0) g_engine_log_fd = open(logp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    static bool installed = false;
    if (installed) return;
    installed = true;
    static const int sigs[] = {SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGABRT};
    for (size_t i = 0; i < sizeof(sigs) / sizeof(sigs[0]); i++) {
        struct sigaction sa; memset(&sa, 0, sizeof(sa));
        sa.sa_sigaction = crash_handler; sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
        sigaction(sigs[i], &sa, &g_prev_sa[sigs[i]]);
    }
}

static void *stdio_forwarder(void *arg) {
    (void)arg; char buf[1024]; ssize_t n;
    while ((n = read(g_stdio_pipe[0], buf, sizeof(buf) - 1)) > 0 || (n < 0 && errno == EINTR)) {
        if (n <= 0) continue;
        buf[n] = '\0';
        __android_log_write(ANDROID_LOG_INFO, "MuffinEngine", buf);
        if (g_engine_log_fd >= 0) (void)!write(g_engine_log_fd, buf, (size_t)n);
    }
    return NULL;
}

static void stdio_redirect_once(void) {
    if (pipe(g_stdio_pipe) != 0) return;
    setvbuf(stdout, NULL, _IOLBF, 0); setvbuf(stderr, NULL, _IONBF, 0);
    if (dup2(g_stdio_pipe[1], STDOUT_FILENO) < 0 || dup2(g_stdio_pipe[1], STDERR_FILENO) < 0) return;
    pthread_t t; if (pthread_create(&t, NULL, stdio_forwarder, NULL) == 0) pthread_detach(t);
}

/* ---- Compatibility C-ABI Symbols for Optional Native Extensions ---- */
__attribute__((visibility("default"))) int32_t tc_android_init_egl(int32_t *out_w, int32_t *out_h) {
    pthread_mutex_lock(&g_lock);
    if (g_pending_window) {
        if (g_window) ANativeWindow_release(g_window);
        g_window = g_pending_window; g_pending_window = NULL; g_surface_dirty = false;
    }
    ANativeWindow *win = g_window;
    int w = g_win_w > 0 ? g_win_w : 1280, h = g_win_h > 0 ? g_win_h : 720;
    pthread_mutex_unlock(&g_lock);
    if (out_w) *out_w = w; if (out_h) *out_h = h;
    if (!win || !load_gl_procs(&g_ext_gl)) return 0;
    if (!g_ext_gl.gl_ready && !init_egl_surface(&g_ext_gl, win)) return 0;
    return 1;
}
__attribute__((visibility("default"))) int32_t mc_android_init_egl(int32_t *ow, int32_t *oh) { return tc_android_init_egl(ow, oh); }

__attribute__((visibility("default"))) void tc_android_swap_buffers(void) {
    if (g_ext_gl.gl_ready && g_ext_gl.eglSwapBuffers) {
        g_ext_gl.eglSwapBuffers(g_ext_gl.dpy, g_ext_gl.surf);
        __atomic_add_fetch(&g_swap_count, 1, __ATOMIC_RELAXED);
    }
}
__attribute__((visibility("default"))) void mc_android_swap_buffers(void) { tc_android_swap_buffers(); }

__attribute__((visibility("default"))) void tc_android_poll_snapshot(MuffinPollSnapshot *out) {
    if (!out) return;
    memset(out, 0, sizeof(*out));
    pthread_mutex_lock(&g_lock);
    out->width = g_win_w > 0 ? g_win_w : 1280; out->height = g_win_h > 0 ? g_win_h : 720;
    float mx = g_tc_input.move_x, my = g_tc_input.move_y;
    out->key_w = g_tc_input.key_w || (my > 0.28f); out->key_s = g_tc_input.key_s || (my < -0.28f);
    out->key_a = g_tc_input.key_a || (mx < -0.28f); out->key_d = g_tc_input.key_d || (mx > 0.28f);
    out->key_jump = g_tc_input.key_jump; out->key_sneak = g_tc_input.key_sneak; out->key_sprint = g_tc_input.key_sprint;
    out->look_dx = g_tc_input.look_dx; out->look_dy = g_tc_input.look_dy;
    g_tc_input.look_dx = 0; g_tc_input.look_dy = 0;
    pthread_mutex_unlock(&g_lock);
}
__attribute__((visibility("default"))) void mc_android_poll_snapshot(MuffinPollSnapshot *out) { tc_android_poll_snapshot(out); }
__attribute__((visibility("default"))) int32_t tc_android_has_window(void) { return (g_window || g_pending_window) ? 1 : 0; }
__attribute__((visibility("default"))) void tc_android_get_size(int32_t *w, int32_t *h) {
    if (w) *w = g_win_w; if (h) *h = g_win_h;
}

/* ---- JNI Entry Points ---- */
JNIEXPORT jstring JNICALL Java_com_muffin_launcher_NativeBridge_nativeProbeHardware(JNIEnv *env, jclass cls) {
    (void)cls;
    char vk_info[64] = "Vulkan: Yok";
    void *hvk = dlopen("libvulkan.so", RTLD_NOW);
    if (hvk) {
        typedef int (*PFN_vkEnumVer)(uint32_t *);
        PFN_vkEnumVer ev = (PFN_vkEnumVer)dlsym(hvk, "vkEnumerateInstanceVersion");
        uint32_t ver = (1u << 22); if (ev) ev(&ver);
        snprintf(vk_info, sizeof(vk_info), "Vulkan %u.%u", (ver >> 22) & 0x7fu, (ver >> 12) & 0x3ffu);
    }
    long cpus = sysconf(_SC_NPROCESSORS_ONLN), pages = sysconf(_SC_PHYS_PAGES), pg_sz = sysconf(_SC_PAGESIZE);
    double ram_gb = (pages > 0 && pg_sz > 0) ? ((double)pages * (double)pg_sz) / (1024.0 * 1024.0 * 1024.0) : 0.0;
#if defined(__aarch64__)
    const char *abi = "arm64-v8a";
#else
    const char *abi = "x86_64";
#endif
    char out[512];
    snprintf(out, sizeof(out), "Mojo 1.2 Chunk Engine (%s) | %s | GLES 3.0+ | %ld Cekirdek | %.1f GB RAM",
             abi, vk_info, cpus > 0 ? cpus : 1, ram_gb);
    return (*env)->NewStringUTF(env, out);
}

JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativeUploadBlockAtlas(
    JNIEnv *env, jclass cls, jbyteArray rgba, jint w, jint h) {
    (void)cls;
    if (!rgba || w <= 0 || h <= 0) return;
    jsize n = (*env)->GetArrayLength(env, rgba);
    if (n < w * h * 4) return;
    pthread_mutex_lock(&g_lock);
    g_atlas_rgba = (uint8_t *)realloc(g_atlas_rgba, (size_t)n);
    (*env)->GetByteArrayRegion(env, rgba, 0, n, (jbyte *)g_atlas_rgba);
    g_atlas_w = w; g_atlas_h = h;
    g_atlas_dirty = true;
    pthread_mutex_unlock(&g_lock);
}

JNIEXPORT jint JNICALL Java_com_muffin_launcher_NativeBridge_nativePollSoundEvent(JNIEnv *env, jclass cls) {
    (void)env; (void)cls;
    return (jint)__atomic_exchange_n(&g_sound_event, 0u, __ATOMIC_RELAXED);
}

JNIEXPORT jint JNICALL Java_com_muffin_launcher_NativeBridge_nativeInitEngine(
    JNIEnv *env, jclass cls,
    jstring username, jstring extLibPath, jstring gameDir, jstring resourcesDir, jstring assetsDir,
    jint backendPref, jint powerProfile, jint renderDist, jint targetFps, jfloat fovDeg) {
    (void)cls; (void)extLibPath;
    pthread_once(&g_stdio_once, stdio_redirect_once);
    pthread_mutex_lock(&g_lock);
    const char *u = username ? (*env)->GetStringUTFChars(env, username, NULL) : NULL;
    if (u) { snprintf(g_username, sizeof(g_username), "%s", u); (*env)->ReleaseStringUTFChars(env, username, u); }
    const char *gd = gameDir ? (*env)->GetStringUTFChars(env, gameDir, NULL) : NULL;
    if (gd) { setenv("MC_GAME_DIR", gd, 1); (void)chdir(gd); install_crash_handler(gd); (*env)->ReleaseStringUTFChars(env, gameDir, gd); }
    const char *rd = resourcesDir ? (*env)->GetStringUTFChars(env, resourcesDir, NULL) : NULL;
    if (rd) { setenv("MUFFIN_RESOURCES", rd, 1); (*env)->ReleaseStringUTFChars(env, resourcesDir, rd); }
    const char *ad = assetsDir ? (*env)->GetStringUTFChars(env, assetsDir, NULL) : NULL;
    if (ad) { setenv("MC_ASSETS_DIR", ad, 1); (*env)->ReleaseStringUTFChars(env, assetsDir, ad); }

    g_ext_loaded = false; g_ext_launched = false; g_ext_entry_fn = NULL;
    g_backend_pref = backendPref; g_power_profile = powerProfile;
    memset(&g_tc_input, 0, sizeof(g_tc_input));
    g_tc_input.sensitivity = 1.0f; g_tc_input.fov_deg = fovDeg;
    g_tc_input.render_dist = renderDist; g_tc_input.target_fps = targetFps;

    init_world_multicore();
    if (!g_thread_running) {
        g_stop_req = false;
        pthread_attr_t attr; pthread_attr_init(&attr);
        pthread_attr_setstacksize(&attr, 4 * 1024 * 1024);
        g_thread_running = (pthread_create(&g_render_thread, &attr, render_loop, NULL) == 0);
        pthread_attr_destroy(&attr);
    }
    pthread_mutex_unlock(&g_lock);
    return 0;
}

JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativeAttachSurface(JNIEnv *env, jclass cls, jobject surf) {
    (void)cls;
    ANativeWindow *w = surf ? ANativeWindow_fromSurface(env, surf) : NULL;
    pthread_mutex_lock(&g_lock);
    if (g_pending_window) ANativeWindow_release(g_pending_window);
    g_pending_window = w;
    if (w) { g_win_w = ANativeWindow_getWidth(w); g_win_h = ANativeWindow_getHeight(w); }
    g_surface_dirty = true;
    pthread_mutex_unlock(&g_lock);
}

JNIEXPORT jintArray JNICALL Java_com_muffin_launcher_NativeBridge_nativeSurfaceSizes(JNIEnv *env, jclass cls) {
    (void)cls; jint v[5];
    pthread_mutex_lock(&g_lock);
    v[0] = g_win_w; v[1] = g_win_h; v[2] = g_egl_w; v[3] = g_egl_h;
    pthread_mutex_unlock(&g_lock);
    v[4] = __atomic_load_n(&g_swap_count, __ATOMIC_RELAXED);
    jintArray a = (*env)->NewIntArray(env, 5);
    if (a) (*env)->SetIntArrayRegion(env, a, 0, 5, v);
    return a;
}

JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativeResizeSurface(JNIEnv *env, jclass cls, jint w, jint h) {
    (void)env; (void)cls;
    pthread_mutex_lock(&g_lock); g_win_w = w; g_win_h = h; pthread_mutex_unlock(&g_lock);
}

JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativeDetachSurface(JNIEnv *env, jclass cls) {
    (void)env; (void)cls;
    pthread_mutex_lock(&g_lock);
    if (g_pending_window) { ANativeWindow_release(g_pending_window); g_pending_window = NULL; }
    g_surface_dirty = true;
    pthread_mutex_unlock(&g_lock);
}

JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativeShutdown(JNIEnv *env, jclass cls) {
    (void)env; (void)cls;
    pthread_t th; bool join = false;
    pthread_mutex_lock(&g_lock);
    if (g_thread_running) { g_stop_req = true; th = g_render_thread; g_thread_running = false; join = true; }
    pthread_mutex_unlock(&g_lock);
    if (join) pthread_join(th, NULL);
    mc_mojo_shutdown(&g_mojo_state);
}

JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativeSetKey(JNIEnv *env, jclass cls, jint key, jboolean down) {
    (void)env; (void)cls;
    uint8_t v = down ? 1 : 0;
    switch (key) {
        case KEY_W: g_tc_input.key_w = v; break; case KEY_A: g_tc_input.key_a = v; break;
        case KEY_S: g_tc_input.key_s = v; break; case KEY_D: g_tc_input.key_d = v; break;
        case KEY_SPACE: g_tc_input.key_jump = v; break; case KEY_LSHIFT: g_tc_input.key_sneak = v; break;
        case KEY_LCTRL: g_tc_input.key_sprint = v; break; case KEY_ESC: g_tc_input.key_esc = v; break;
        case KEY_E: g_tc_input.key_e = v; break; case KEY_Q: g_tc_input.key_q = v; break;
        case KEY_F: g_tc_input.key_f = v; break; case KEY_T: g_tc_input.key_t = v; break;
        case KEY_F3: g_tc_input.key_f3 = v; break; case KEY_TAB: g_tc_input.key_tab = v; break;
        case KEY_F5: g_tc_input.key_f5 = v; if (down) g_tc_input.third_person = !g_tc_input.third_person; break;
        default: break;
    }
}

JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativeSetMoveAxis(JNIEnv *env, jclass cls, jfloat x, jfloat y) {
    (void)env; (void)cls; g_tc_input.move_x = x; g_tc_input.move_y = y;
}
JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativeAddLookDelta(JNIEnv *env, jclass cls, jfloat dx, jfloat dy) {
    (void)env; (void)cls; g_tc_input.look_dx += dx; g_tc_input.look_dy += dy;
}
JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativeMouseButton(JNIEnv *env, jclass cls, jint btn, jboolean down) {
    (void)env; (void)cls;
    if (btn == 0) { g_tc_input.key_lmb = down ? 1 : 0; if (down) g_tc_input.req_break = 1; }
    else if (btn == 1) { g_tc_input.key_rmb = down ? 1 : 0; if (down) g_tc_input.req_place = 1; }
}
JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativeSelectHotbar(JNIEnv *env, jclass cls, jint slot) {
    (void)env; (void)cls; if (slot >= 0 && slot < 9) g_tc_input.hotbar_slot = slot;
}
JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativeSetCursorMode(JNIEnv *env, jclass cls, jboolean visible, jfloat nx, jfloat ny) {
    (void)env; (void)cls; g_tc_input.cursor_visible = visible ? 1 : 0; g_tc_input.cursor_x = nx; g_tc_input.cursor_y = ny;
}
JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativeUpdateRuntimeConfig(
    JNIEnv *env, jclass cls, jfloat sens, jboolean invY, jfloat fov, jint rdist, jint fps) {
    (void)env; (void)cls;
    g_tc_input.sensitivity = sens; g_tc_input.invert_y = invY ? 1 : 0;
    g_tc_input.fov_deg = fov; g_tc_input.render_dist = rdist; g_tc_input.target_fps = fps;
}
JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativePollTelemetry(JNIEnv *env, jclass cls, jlongArray outArr) {
    (void)cls; if (!outArr || (*env)->GetArrayLength(env, outArr) < 16) return;
    jlong buf[16] = {0};
    pthread_mutex_lock(&g_lock);
    buf[0] = (jlong)(g_fps * 10.0f); buf[1] = (jlong)g_tel.frame_epoch;
    buf[2] = (jlong)g_tel.arena_bytes; buf[3] = (jlong)g_tel.slab_committed_bytes;
    buf[4] = (jlong)g_tel.slab_reclaimed_bytes; buf[5] = (jlong)g_tel.visible_sections;
    buf[6] = (jlong)g_tel.frustum_culled; buf[7] = (jlong)g_tel.portal_culled;
    buf[8] = (jlong)g_tel.hiz_culled; buf[9] = (jlong)g_tel.draw_batches;
    buf[10] = (jlong)g_tel.surviving_instances; buf[11] = (jlong)g_tel.worker_threads;
    buf[12] = (jlong)g_tel.rhi_active_backend; buf[13] = (jlong)(g_px * 10.0f);
    buf[14] = (jlong)(g_py * 10.0f); buf[15] = (jlong)(g_pz * 10.0f);
    pthread_mutex_unlock(&g_lock);
    (*env)->SetLongArrayRegion(env, outArr, 0, 16, buf);
}
JNIEXPORT jstring JNICALL Java_com_muffin_launcher_NativeBridge_nativeGetLastLog(JNIEnv *env, jclass cls) {
    (void)cls; pthread_mutex_lock(&g_lock);
    jstring s = (*env)->NewStringUTF(env, g_last_log);
    pthread_mutex_unlock(&g_lock); return s;
}
