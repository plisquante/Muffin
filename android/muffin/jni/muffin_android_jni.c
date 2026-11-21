/*
 * Muffin Native Android Host & EGL/GLES3/Vulkan JNI Bridge (`libmuffin_core.so`).
 *
 * Links directly with Mojo 1.2 native C-ABI objects (`muffin_core.mojo` compiled for
 * arm64-v8a & x86_64). Supports BOTH:
 * 1. Built-in Mojo 1.2 Zero-GC Voxel Engine (`mc_mojo_init` / `mc_mojo_tick_frame` / `mc_mojo_shutdown`)
 * 2. External compiled Minecraft native engine `.so` (`muffin_android_entry` or `j2z_android_entry`),
 *    providing GLES 3.0+ EGL surface lifecycle, stdout/stderr redirect, SIGSEGV backtrace capture,
 *    and multi-touch / keyboard input snapshots (`mc_android_*` & `tc_android_*`).
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
#include <inttypes.h>
#include <signal.h>
#include <ucontext.h>
#include <unwind.h>
#include <sys/prctl.h>
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
#define GL_BACK 0x0405
#define GL_TRIANGLES 0x0004
#define GL_FLOAT 0x1406
#define GL_FALSE 0
#define GL_ARRAY_BUFFER 0x8892
#define GL_STATIC_DRAW 0x88E4
#define GL_FRAGMENT_SHADER 0x8B30
#define GL_VERTEX_SHADER 0x8B31
#define GL_COMPILE_STATUS 0x8B81
#define GL_LINK_STATUS 0x8B82
#define GL_VENDOR 0x1F00
#define GL_RENDERER 0x1F01
#define GL_VERSION 0x1F02

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
typedef void (*PFN_glCullFace)(GLenum);
typedef const unsigned char *(*PFN_glGetString)(GLenum);
typedef GLenum (*PFN_glGetError)(void);
typedef void (*PFN_glGetIntegerv)(GLenum, GLint *);
typedef unsigned char (*PFN_glIsEnabled)(GLenum);
typedef void (*PFN_glReadPixels_)(GLint, GLint, int, int, GLenum, GLenum, void *);
typedef void (*PFN_glBindFramebuffer_)(GLenum, unsigned int);
typedef void (*PFN_glPixelStorei_)(GLenum, GLint);
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
typedef void (*PFN_glUniform4f)(GLint, GLfloat, GLfloat, GLfloat, GLfloat);
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

extern int32_t mc_mojo_init(MuffinMojoState *st, int32_t backend_pref, int32_t power_profile, int32_t worker_count);
extern void mc_mojo_tick_frame(
    MuffinMojoState *st,
    float cam_x, float cam_y, float cam_z,
    float look_x, float look_y, float look_z,
    float fov_deg, uint32_t vw, uint32_t vh,
    uint32_t entity_count, MuffinMojoTelemetry *out_tel);
extern void mc_mojo_shutdown(MuffinMojoState *st);

/* ---- GLFW key constants & shared touch input struct ---- */
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
    volatile uint8_t key_w;
    volatile uint8_t key_a;
    volatile uint8_t key_s;
    volatile uint8_t key_d;
    volatile uint8_t key_jump;
    volatile uint8_t key_sneak;
    volatile uint8_t key_sprint;
    volatile uint8_t key_lmb;
    volatile uint8_t key_rmb;
    volatile uint8_t key_esc;
    volatile uint8_t key_e;
    volatile uint8_t key_q;
    volatile uint8_t key_f;
    volatile uint8_t key_t;
    volatile uint8_t key_f3;
    volatile uint8_t key_f5;
    volatile uint8_t key_tab;
    volatile uint8_t req_break;
    volatile uint8_t req_place;
    volatile uint8_t third_person;
    volatile float move_x;
    volatile float move_y;
    volatile float look_dx;
    volatile float look_dy;
    volatile int32_t hotbar_slot;
    volatile float sensitivity;
    volatile uint8_t invert_y;
    volatile float fov_deg;
    volatile int32_t render_dist;
    volatile int32_t target_fps;
    volatile uint8_t cursor_visible;
    volatile float cursor_x;
    volatile float cursor_y;
} MuffinInputState;

typedef struct {
    int32_t width;
    int32_t height;
    uint8_t key_w, key_a, key_s, key_d, key_jump, key_sneak, key_sprint, key_lmb, key_rmb;
    uint8_t key_esc, key_e, key_q, key_f, key_t, key_f3, key_f5, key_tab;
    uint8_t third_person, cursor_visible;
    uint8_t _pad[2];
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
static int g_win_w = 1280;
static int g_win_h = 720;
static int g_egl_w = 0, g_egl_h = 0;
static volatile int32_t g_swap_count = 0;
static int g_backend_pref = 0;
static int g_power_profile = 1;
static MuffinMojoState g_mojo_state = {0};
static MuffinMojoTelemetry g_tel = {0};
static float g_fps = 0.0f;
static char g_username[64] = "MojoOyuncu";
static char g_ext_lib_path[512] = "";
static void *g_ext_handle = NULL;
static PFN_engine_main g_ext_entry_fn = NULL;
static bool g_ext_loaded = false;
static bool g_ext_launched = false;
static char g_last_log[2048] = "Muffin Mojo 1.2 Native Engine hazir.";

static void set_log(const char *msg) {
    pthread_mutex_lock(&g_lock);
    snprintf(g_last_log, sizeof(g_last_log), "%s", msg ? msg : "");
    pthread_mutex_unlock(&g_lock);
    LOGI("%s", msg ? msg : "");
}

/* ---- Interactive 3D Mojo World ---- */
#define WX 32
#define WY 24
#define WZ 32
static uint8_t g_world[WX][WY][WZ];
static float g_px = 16.5f, g_py = 11.0f, g_pz = 16.5f;
static float g_vy = 0.0f;
static float g_yaw = 0.0f, g_pitch = -0.10f;
static bool g_on_ground = false;
static int g_hit_bx = -1, g_hit_by = -1, g_hit_bz = -1;
static int g_place_bx = -1, g_place_by = -1, g_place_bz = -1;

static void init_sandbox_world(void) {
    memset(g_world, 0, sizeof(g_world));
    for (int x = 0; x < WX; x++) {
        for (int z = 0; z < WZ; z++) {
            int dx = x - 16, dz = z - 16;
            float hill = 2.6f * sinf(x * 0.25f) * cosf(z * 0.25f) + 1.4f * cosf((x + z) * 0.18f);
            int h = 7 + (int)hill;
            if (dx * dx + dz * dz < 18) h = 8;
            if (h < 3) h = 3;
            if (h > WY - 6) h = WY - 6;
            for (int y = 0; y <= h; y++) {
                if (y == 0) g_world[x][y][z] = 7;
                else if (y < h - 2) g_world[x][y][z] = ((x * 7 + y * 13 + z * 5) % 19 == 0) ? 8 : 3;
                else if (y < h) g_world[x][y][z] = 2;
                else g_world[x][y][z] = 1;
            }
        }
    }
    /* Mojo Edition Cyan-Blue Crystalline Portal Shrine & Beacon */
    for (int y = 9; y <= 13; y++) {
        g_world[14][y][10] = 7;
        g_world[18][y][10] = 7;
    }
    for (int x = 14; x <= 18; x++) g_world[x][13][10] = 4;
    g_world[16][14][10] = 8; /* Mojo Cyan Diamond Crystal Core */
    g_world[15][9][10] = 9;
    g_world[17][9][10] = 9;
    g_world[16][9][12] = 8;

    int tx[3] = {8, 23, 22};
    int tz[3] = {9, 10, 23};
    for (int t = 0; t < 3; t++) {
        int bx = tx[t], bz = tz[t], base = 4;
        for (int y = WY - 2; y >= 1; y--) {
            if (g_world[bx][y][bz] != 0) { base = y + 1; break; }
        }
        for (int y = 0; y < 4 && base + y < WY - 2; y++) g_world[bx][base + y][bz] = 5;
        for (int lx = -2; lx <= 2; lx++) {
            for (int lz = -2; lz <= 2; lz++) {
                for (int ly = 2; ly <= 4; ly++) {
                    if (abs(lx) + abs(lz) + (ly == 4 ? 1 : 0) <= 3) {
                        int px = bx + lx, py = base + ly, pz = bz + lz;
                        if (px >= 0 && px < WX && py >= 0 && py < WY && pz >= 0 && pz < WZ) {
                            if (g_world[px][py][pz] == 0) g_world[px][py][pz] = 6;
                        }
                    }
                }
            }
        }
    }
    g_px = 16.5f; g_py = 11.0f; g_pz = 16.5f;
    g_vy = 0.0f; g_yaw = 0.0f; g_pitch = -0.10f;
}

static bool is_solid_at(float x, float y, float z) {
    int bx = (int)floorf(x), by = (int)floorf(y), bz = (int)floorf(z);
    if (bx < 0 || bx >= WX || bz < 0 || bz >= WZ) return true;
    if (by < 0) return true;
    if (by >= WY) return false;
    return g_world[bx][by][bz] != 0;
}

static bool player_collides(float x, float y, float z) {
    const float r = 0.28f, h = 1.65f;
    return is_solid_at(x - r, y, z - r) || is_solid_at(x + r, y, z - r) ||
           is_solid_at(x - r, y, z + r) || is_solid_at(x + r, y, z + r) ||
           is_solid_at(x - r, y + 0.9f, z - r) || is_solid_at(x + r, y + 0.9f, z + r) ||
           is_solid_at(x - r, y + h, z - r) || is_solid_at(x + r, y + h, z + r);
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

    float fwd = g_tc_input.move_y + (g_tc_input.key_w ? 1.0f : 0.0f) - (g_tc_input.key_s ? 1.0f : 0.0f);
    float strafe = g_tc_input.move_x + (g_tc_input.key_d ? 1.0f : 0.0f) - (g_tc_input.key_a ? 1.0f : 0.0f);
    float len = sqrtf(fwd * fwd + strafe * strafe);
    if (len > 1.0f) { fwd /= len; strafe /= len; }

    float speed = 4.6f;
    if (g_tc_input.key_sprint && fwd > 0.1f && !g_tc_input.key_sneak) speed = 7.4f;
    if (g_tc_input.key_sneak) speed = 2.0f;

    float sx = sinf(g_yaw), cx = cosf(g_yaw);
    float vx = (sx * fwd + cx * strafe) * speed * dt;
    float vz = (-cx * fwd + sx * strafe) * speed * dt;

    if (!player_collides(g_px + vx, g_py, g_pz)) g_px += vx;
    if (!player_collides(g_px, g_py, g_pz + vz)) g_pz += vz;

    if (g_tc_input.key_jump && g_on_ground) {
        g_vy = 7.6f; g_on_ground = false;
    }
    g_vy -= 21.0f * dt;
    float ny = g_py + g_vy * dt;
    if (!player_collides(g_px, ny, g_pz)) {
        g_py = ny; g_on_ground = false;
    } else {
        if (g_vy < 0.0f) g_on_ground = true;
        g_vy = 0.0f;
    }

    float eye_y = g_py + (g_tc_input.key_sneak ? 1.28f : 1.54f);
    float lx = sinf(g_yaw) * cosf(g_pitch);
    float ly = sinf(g_pitch);
    float lz = -cosf(g_yaw) * cosf(g_pitch);
    g_hit_bx = -1; g_place_bx = -1;
    int px_prev = (int)floorf(g_px), py_prev = (int)floorf(eye_y), pz_prev = (int)floorf(g_pz);
    for (float t = 0.2f; t <= 6.2f; t += 0.06f) {
        int bx = (int)floorf(g_px + lx * t), by = (int)floorf(eye_y + ly * t), bz = (int)floorf(g_pz + lz * t);
        if (bx >= 0 && bx < WX && by >= 0 && by < WY && bz >= 0 && bz < WZ && g_world[bx][by][bz] != 0) {
            g_hit_bx = bx; g_hit_by = by; g_hit_bz = bz;
            g_place_bx = px_prev; g_place_by = py_prev; g_place_bz = pz_prev;
            break;
        }
        px_prev = bx; py_prev = by; pz_prev = bz;
    }
    if (g_tc_input.req_break) {
        g_tc_input.req_break = 0;
        if (g_hit_bx >= 0 && g_hit_by > 0) g_world[g_hit_bx][g_hit_by][g_hit_bz] = 0;
    }
    if (g_tc_input.req_place) {
        g_tc_input.req_place = 0;
        if (g_place_bx >= 0 && g_place_bx < WX && g_place_by >= 0 && g_place_by < WY && g_place_bz >= 0 && g_place_bz < WZ) {
            uint8_t cur = g_world[g_place_bx][g_place_by][g_place_bz];
            int slot = g_tc_input.hotbar_slot;
            g_world[g_place_bx][g_place_by][g_place_bz] = (uint8_t)((slot >= 0 && slot < 9) ? (slot + 1) : 1);
            if (player_collides(g_px, g_py, g_pz)) g_world[g_place_bx][g_place_by][g_place_bz] = cur;
        }
    }
}

/* ---- GLES 3.0 Renderer State ---- */
typedef struct {
    void *lib_egl;
    void *lib_gles;
    PFN_eglGetDisplay eglGetDisplay;
    PFN_eglInitialize eglInitialize;
    PFN_eglChooseConfig eglChooseConfig;
    PFN_eglCreateWindowSurface eglCreateWindowSurface;
    PFN_eglCreateContext eglCreateContext;
    PFN_eglMakeCurrent eglMakeCurrent;
    PFN_eglSwapBuffers eglSwapBuffers;
    PFN_eglQuerySurface eglQuerySurface;
    PFN_eglSwapInterval eglSwapInterval;
    PFN_eglDestroySurface eglDestroySurface;
    PFN_eglDestroyContext eglDestroyContext;

    PFN_glViewport glViewport;
    PFN_glClearColor glClearColor;
    PFN_glClear glClear;
    PFN_glEnable glEnable;
    PFN_glDisable glDisable;
    PFN_glCullFace glCullFace;
    PFN_glGetString glGetString;
    PFN_glGetError glGetError_;
    PFN_glGetIntegerv glGetIntegerv_;
    PFN_glIsEnabled glIsEnabled_;
    PFN_glCreateShader glCreateShader;
    PFN_glShaderSource glShaderSource;
    PFN_glCompileShader glCompileShader;
    PFN_glGetShaderiv glGetShaderiv;
    PFN_glDeleteShader glDeleteShader;
    PFN_glCreateProgram glCreateProgram;
    PFN_glAttachShader glAttachShader;
    PFN_glLinkProgram glLinkProgram;
    PFN_glGetProgramiv glGetProgramiv;
    PFN_glDeleteProgram glDeleteProgram;
    PFN_glUseProgram glUseProgram;
    PFN_glGetUniformLocation glGetUniformLocation;
    PFN_glUniformMatrix4fv glUniformMatrix4fv;
    PFN_glUniform3f glUniform3f;
    PFN_glUniform4f glUniform4f;
    PFN_glGenVertexArrays glGenVertexArrays;
    PFN_glBindVertexArray glBindVertexArray;
    PFN_glDeleteVertexArrays glDeleteVertexArrays;
    PFN_glGenBuffers glGenBuffers;
    PFN_glBindBuffer glBindBuffer;
    PFN_glBufferData glBufferData;
    PFN_glDeleteBuffers glDeleteBuffers;
    PFN_glEnableVertexAttribArray glEnableVertexAttribArray;
    PFN_glVertexAttribPointer glVertexAttribPointer;
    PFN_glDrawArrays glDrawArrays;

    EGLDisplay dpy;
    EGLConfig cfg;
    EGLSurface surf;
    EGLContext ctx;
    GLuint prog;
    GLuint vao;
    GLuint vbo;
    GLint u_mvp, u_offset, u_color, u_tint_top;
    bool gl_ready;
} GlState;

static GlState g_ext_gl = {0};

static const char *VS_300 =
    "#version 300 es\n"
    "layout(location=0) in vec3 aPos;\n"
    "layout(location=1) in vec3 aNorm;\n"
    "uniform mat4 uMvp;\n"
    "uniform vec3 uOffset;\n"
    "out float vShade;\n"
    "out float vTop;\n"
    "out vec3 vLocal;\n"
    "out vec3 vNorm;\n"
    "void main() {\n"
    "  vec3 w = aPos + uOffset;\n"
    "  gl_Position = uMvp * vec4(w, 1.0);\n"
    "  float light = 0.68;\n"
    "  if (aNorm.y > 0.5) light = 1.00;\n"
    "  else if (aNorm.y < -0.5) light = 0.48;\n"
    "  else if (abs(aNorm.x) > 0.5) light = 0.82;\n"
    "  else light = 0.74;\n"
    "  vShade = light; vTop = aNorm.y > 0.5 ? 1.0 : 0.0;\n"
    "  vLocal = aPos; vNorm = aNorm;\n"
    "}\n";

static const char *FS_300 =
    "#version 300 es\n"
    "precision mediump float;\n"
    "uniform vec4 uColor;\n"
    "uniform vec4 uTintTop;\n"
    "in float vShade; in float vTop; in vec3 vLocal; in vec3 vNorm;\n"
    "out vec4 oColor;\n"
    "void main() {\n"
    "  vec3 base = mix(uColor.rgb, uTintTop.rgb, vTop * uTintTop.a);\n"
    "  vec2 uv = abs(vNorm.y) > 0.5 ? vLocal.xz : (abs(vNorm.x) > 0.5 ? vLocal.zy : vLocal.xy);\n"
    "  float edge = min(min(uv.x, 1.0 - uv.x), min(uv.y, 1.0 - uv.y));\n"
    "  float border = edge < 0.045 ? 0.84 : 1.0;\n"
    "  float tex = 0.94 + 0.06 * fract(sin(dot(floor(uv * 16.0), vec2(12.9898, 78.233))) * 43758.5453);\n"
    "  oColor = vec4(base * vShade * border * tex, uColor.a);\n"
    "}\n";

static const float CUBE_VERTS[36 * 6] = {
    0,0,1, 0,0,1,  1,0,1, 0,0,1,  1,1,1, 0,0,1,  0,0,1, 0,0,1,  1,1,1, 0,0,1,  0,1,1, 0,0,1,
    1,0,0, 0,0,-1, 0,0,0, 0,0,-1, 0,1,0, 0,0,-1, 1,0,0, 0,0,-1, 0,1,0, 0,0,-1, 1,1,0, 0,0,-1,
    1,0,1, 1,0,0,  1,0,0, 1,0,0,  1,1,0, 1,0,0,  1,0,1, 1,0,0,  1,1,0, 1,0,0,  1,1,1, 1,0,0,
    0,0,0,-1,0,0,  0,0,1,-1,0,0,  0,1,1,-1,0,0,  0,0,0,-1,0,0,  0,1,1,-1,0,0,  0,1,0,-1,0,0,
    0,1,1, 0,1,0,  1,1,1, 0,1,0,  1,1,0, 0,1,0,  0,1,1, 0,1,0,  1,1,0, 0,1,0,  0,1,0, 0,1,0,
    0,0,0, 0,-1,0, 1,0,0, 0,-1,0, 1,0,1, 0,-1,0, 0,0,0, 0,-1,0, 1,0,1, 0,-1,0, 0,0,1, 0,-1,0
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
    g->glCullFace = (PFN_glCullFace)dlsym(g->lib_gles, "glCullFace");
    g->glGetString = (PFN_glGetString)dlsym(g->lib_gles, "glGetString");
    g->glGetError_ = (PFN_glGetError)dlsym(g->lib_gles, "glGetError");
    g->glGetIntegerv_ = (PFN_glGetIntegerv)dlsym(g->lib_gles, "glGetIntegerv");
    g->glIsEnabled_ = (PFN_glIsEnabled)dlsym(g->lib_gles, "glIsEnabled");
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
    g->glUniform4f = (PFN_glUniform4f)dlsym(g->lib_gles, "glUniform4f");
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
    return g->eglGetDisplay && g->glDrawArrays && g->glCreateProgram;
}

static GLuint compile_sh(GlState *g, GLenum type, const char *src) {
    GLuint s = g->glCreateShader(type);
    g->glShaderSource(s, 1, &src, NULL);
    g->glCompileShader(s);
    GLint ok = 0;
    g->glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) { g->glDeleteShader(s); return 0; }
    return s;
}

static bool init_egl_surface(GlState *g, ANativeWindow *win) {
    g->gl_ready = false;
    g->dpy = g->eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (g->dpy == EGL_NO_DISPLAY) return false;
    EGLint maj = 0, min = 0;
    if (!g->eglInitialize(g->dpy, &maj, &min)) return false;

    const EGLint cfg24[] = {
        EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
        EGL_DEPTH_SIZE, 24, EGL_NONE
    };
    const EGLint cfg16[] = {
        EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
        EGL_DEPTH_SIZE, 16, EGL_NONE
    };
    EGLint num = 0;
    if (!g->eglChooseConfig(g->dpy, cfg24, &g->cfg, 1, &num) || num < 1) {
        if (!g->eglChooseConfig(g->dpy, cfg16, &g->cfg, 1, &num) || num < 1) return false;
    }
    g->surf = g->eglCreateWindowSurface(g->dpy, g->cfg, win, NULL);
    if (g->surf == EGL_NO_SURFACE) return false;
    const EGLint ctx_attrs[] = { EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE };
    g->ctx = g->eglCreateContext(g->dpy, g->cfg, EGL_NO_CONTEXT, ctx_attrs);
    if (g->ctx == EGL_NO_CONTEXT) return false;
    if (!g->eglMakeCurrent(g->dpy, g->surf, g->surf, g->ctx)) return false;
    if (g->eglSwapInterval) g->eglSwapInterval(g->dpy, g_power_profile == 0 ? 0 : 1);

    GLuint vs = compile_sh(g, GL_VERTEX_SHADER, VS_300);
    GLuint fs = compile_sh(g, GL_FRAGMENT_SHADER, FS_300);
    if (!vs || !fs) return false;
    g->prog = g->glCreateProgram();
    g->glAttachShader(g->prog, vs); g->glAttachShader(g->prog, fs);
    g->glLinkProgram(g->prog);
    g->glDeleteShader(vs); g->glDeleteShader(fs);
    GLint link_ok = 0;
    g->glGetProgramiv(g->prog, GL_LINK_STATUS, &link_ok);
    if (!link_ok) return false;

    g->u_mvp = g->glGetUniformLocation(g->prog, "uMvp");
    g->u_offset = g->glGetUniformLocation(g->prog, "uOffset");
    g->u_color = g->glGetUniformLocation(g->prog, "uColor");
    g->u_tint_top = g->glGetUniformLocation(g->prog, "uTintTop");

    g->glGenVertexArrays(1, &g->vao);
    g->glBindVertexArray(g->vao);
    g->glGenBuffers(1, &g->vbo);
    g->glBindBuffer(GL_ARRAY_BUFFER, g->vbo);
    g->glBufferData(GL_ARRAY_BUFFER, sizeof(CUBE_VERTS), CUBE_VERTS, GL_STATIC_DRAW);
    g->glEnableVertexAttribArray(0);
    g->glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 6 * sizeof(float), (void *)0);
    g->glEnableVertexAttribArray(1);
    g->glVertexAttribPointer(1, 3, GL_FLOAT, GL_FALSE, 6 * sizeof(float), (void *)(3 * sizeof(float)));
    g->glEnable(GL_DEPTH_TEST);
    g->glEnable(GL_CULL_FACE);
    g->glCullFace(GL_BACK);
    g->gl_ready = true;
    return true;
}

static void destroy_egl(GlState *g) {
    g->gl_ready = false;
    if (g->dpy != EGL_NO_DISPLAY) {
        if (g->eglMakeCurrent) g->eglMakeCurrent(g->dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (g->prog && g->glDeleteProgram) g->glDeleteProgram(g->prog);
        if (g->vbo && g->glDeleteBuffers) g->glDeleteBuffers(1, &g->vbo);
        if (g->vao && g->glDeleteVertexArrays) g->glDeleteVertexArrays(1, &g->vao);
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
    float nearp = 0.08f, farp = 180.0f, nf = 1.0f / (nearp - farp);
    float p[16] = {
        f / aspect, 0, 0, 0,
        0, f, 0, 0,
        0, 0, (farp + nearp) * nf, -1.0f,
        0, 0, 2.0f * farp * nearp * nf, 0
    };
    float rx = -fz, ry = 0.0f, rz = fx;
    float rlen = sqrtf(rx * rx + rz * rz);
    if (rlen > 1e-6f) { rx /= rlen; rz /= rlen; } else { rx = 1.0f; rz = 0.0f; }
    float ux = ry * fz - rz * fy, uy = rz * fx - rx * fz, uz = rx * fy - ry * fx;
    float v[16] = {
        rx, ux, -fx, 0,  ry, uy, -fy, 0,  rz, uz, -fz, 0,
        -(rx * ex + ry * ey + rz * ez), -(ux * ex + uy * ey + uz * ez), (fx * ex + fy * ey + fz * ez), 1.0f
    };
    for (int col = 0; col < 4; col++) {
        for (int row = 0; row < 4; row++) {
            float sum = 0.0f;
            for (int k = 0; k < 4; k++) sum += p[k * 4 + row] * v[col * 4 + k];
            out16[col * 4 + row] = sum;
        }
    }
}

static void block_color(uint8_t id, float *c, float *top) {
    top[3] = 0.0f;
    switch (id) {
        case 1: c[0]=0.48f; c[1]=0.33f; c[2]=0.22f; c[3]=1; top[0]=0.27f; top[1]=0.68f; top[2]=0.26f; top[3]=1.0f; break;
        case 2: c[0]=0.52f; c[1]=0.36f; c[2]=0.23f; c[3]=1; break;
        case 3: c[0]=0.50f; c[1]=0.52f; c[2]=0.55f; c[3]=1; break;
        case 4: c[0]=0.74f; c[1]=0.58f; c[2]=0.34f; c[3]=1; break;
        case 5: c[0]=0.38f; c[1]=0.25f; c[2]=0.15f; c[3]=1; break;
        case 6: c[0]=0.18f; c[1]=0.54f; c[2]=0.21f; c[3]=1; break;
        case 7: c[0]=0.35f; c[1]=0.37f; c[2]=0.40f; c[3]=1; break;
        case 8: c[0]=0.14f; c[1]=0.76f; c[2]=0.96f; c[3]=1; break; /* Mojo Diamond Blue Core */
        case 9: c[0]=0.99f; c[1]=0.89f; c[2]=0.42f; c[3]=1; break;
        default: c[0]=0.8f; c[1]=0.8f; c[2]=0.8f; c[3]=1; break;
    }
}

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static void *render_loop(void *arg) {
    (void)arg;
    GlState gl = {0};
    if (!load_gl_procs(&gl)) {
        set_log("HATA: libEGL.so / libGLESv3.so yuklenemedi");
        return NULL;
    }
    int32_t bk = mc_mojo_init(&g_mojo_state, g_backend_pref, g_power_profile, 4);
    const char *mc_ver = getenv("MC_VERSION");
    char msg[256];
    snprintf(msg, sizeof(msg), "Mojo 1.2 Native Engine aktif (backend=%d, user=%s, version=%s)",
             bk, g_username, mc_ver ? mc_ver : "Mojo-Voxel");
    set_log(msg);

    uint64_t prev_ns = now_ns(), fps_acc_ns = 0;
    uint32_t fps_frames = 0;
    while (!g_stop_req) {
        pthread_mutex_lock(&g_lock);
        if (g_surface_dirty) {
            g_surface_dirty = false;
            if (g_window) {
                destroy_egl(&gl);
                load_gl_procs(&gl);
                ANativeWindow_release(g_window);
                g_window = NULL;
            }
            g_window = g_pending_window;
            g_pending_window = NULL;
            if (g_window) {
                if (!init_egl_surface(&gl, g_window)) {
                    destroy_egl(&gl);
                    load_gl_procs(&gl);
                    set_log("HATA: EGL/GLES3 surface baslatilamadi");
                } else {
                    const char *ren = gl.glGetString ? (const char *)gl.glGetString(GL_RENDERER) : "GLES3";
                    snprintf(msg, sizeof(msg), "EGL+GLES3 baglandi: %s (%dx%d)", ren ? ren : "GPU", g_win_w, g_win_h);
                    set_log(msg);
                }
            }
        }
        int w = g_win_w > 0 ? g_win_w : 1280;
        int h = g_win_h > 0 ? g_win_h : 720;
        ANativeWindow *cur_win = g_window;
        pthread_mutex_unlock(&g_lock);

        if (!cur_win || !gl.gl_ready) {
            usleep(16000);
            prev_ns = now_ns();
            continue;
        }

        uint64_t cur_ns = now_ns();
        float dt = (float)(cur_ns - prev_ns) / 1e9f;
        prev_ns = cur_ns;
        step_player(dt);

        float eye_y = g_py + (g_tc_input.key_sneak ? 1.28f : 1.54f);
        float fx = sinf(g_yaw) * cosf(g_pitch), fy = sinf(g_pitch), fz = -cosf(g_yaw) * cosf(g_pitch);
        float cam_x = g_px, cam_y = eye_y, cam_z = g_pz;
        if (g_tc_input.third_person) {
            cam_x -= fx * 3.5f; cam_y -= fy * 3.5f - 0.5f; cam_z -= fz * 3.5f;
        }

        MuffinMojoTelemetry tel = {0};
        float fov = g_tc_input.fov_deg > 30.0f ? g_tc_input.fov_deg : 75.0f;
        mc_mojo_tick_frame(&g_mojo_state, cam_x, cam_y, cam_z,
                           cam_x + fx * 16.0f, cam_y + fy * 16.0f, cam_z + fz * 16.0f,
                           fov, (uint32_t)w, (uint32_t)h, 48, &tel);

        gl.glViewport(0, 0, w, h);
        gl.glClearColor(0.42f, 0.72f, 0.96f, 1.0f);
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
        gl.glUseProgram(gl.prog);
        gl.glBindVertexArray(gl.vao);

        float mvp[16];
        build_mvp(mvp, (float)w / (float)h, fov, cam_x, cam_y, cam_z, fx, fy, fz);
        gl.glUniformMatrix4fv(gl.u_mvp, 1, GL_FALSE, mvp);

        int max_r = (g_tc_input.render_dist > 1 ? g_tc_input.render_dist * 3 : 16);
        for (int x = 0; x < WX; x++) {
            if (fabsf((float)x - g_px) > (float)max_r) continue;
            for (int z = 0; z < WZ; z++) {
                if (fabsf((float)z - g_pz) > (float)max_r) continue;
                for (int y = 0; y < WY; y++) {
                    uint8_t id = g_world[x][y][z];
                    if (id == 0) continue;
                    bool exposed = (x == 0 || x == WX - 1 || y == 0 || y == WY - 1 || z == 0 || z == WZ - 1 ||
                                    g_world[x-1][y][z] == 0 || g_world[x+1][y][z] == 0 ||
                                    g_world[x][y-1][z] == 0 || g_world[x][y+1][z] == 0 ||
                                    g_world[x][y][z-1] == 0 || g_world[x][y][z+1] == 0);
                    if (!exposed) continue;
                    float col[4], top[4];
                    block_color(id, col, top);
                    if (x == g_hit_bx && y == g_hit_by && z == g_hit_bz) {
                        for (int k = 0; k < 3; k++) {
                            col[k] = fminf(1.0f, col[k] + 0.22f);
                            top[k] = fminf(1.0f, top[k] + 0.22f);
                        }
                    }
                    gl.glUniform3f(gl.u_offset, (float)x, (float)y, (float)z);
                    gl.glUniform4f(gl.u_color, col[0], col[1], col[2], col[3]);
                    gl.glUniform4f(gl.u_tint_top, top[0], top[1], top[2], top[3]);
                    gl.glDrawArrays(GL_TRIANGLES, 0, 36);
                }
            }
        }
        if (g_tc_input.third_person) {
            gl.glUniform3f(gl.u_offset, g_px - 0.35f, g_py, g_pz - 0.35f);
            gl.glUniform4f(gl.u_color, 0.14f, 0.76f, 0.96f, 1.0f);
            gl.glUniform4f(gl.u_tint_top, 0.38f, 0.88f, 1.00f, 1.0f);
            gl.glDrawArrays(GL_TRIANGLES, 0, 36);
        }

        gl.eglSwapBuffers(gl.dpy, gl.surf);
        __atomic_add_fetch(&g_swap_count, 1, __ATOMIC_RELAXED);

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

/* ---- Crash / stdout diagnostics ---- */
static int g_stdio_pipe[2] = {-1, -1};
static int g_engine_log_fd = -1;
static char g_crash_path[512];
static struct sigaction g_prev_sa[32];
static pthread_once_t g_stdio_once = PTHREAD_ONCE_INIT;

static void crash_puts(int fd, const char *s) {
    size_t n = strlen(s);
    while (n > 0) {
        ssize_t w = write(fd, s, n);
        if (w <= 0) return;
        s += w; n -= (size_t)w;
    }
}

static void crash_handler(int sig, siginfo_t *si, void *ucv) {
    (void)ucv;
    int fd = g_crash_path[0] ? open(g_crash_path, O_WRONLY | O_CREAT | O_TRUNC, 0644) : -1;
    if (fd >= 0) {
        char buf[256];
        snprintf(buf, sizeof(buf), "signal %d (code %d) addr %p\n", sig, si ? si->si_code : 0, si ? si->si_addr : NULL);
        crash_puts(fd, buf);
        close(fd);
    }
    if (g_engine_log_fd >= 0) fsync(g_engine_log_fd);
    struct sigaction *prev = (sig > 0 && sig < 32) ? &g_prev_sa[sig] : NULL;
    if (prev && (prev->sa_flags & SA_SIGINFO) && prev->sa_sigaction) prev->sa_sigaction(sig, si, ucv);
    else { signal(sig, SIG_DFL); raise(sig); }
}

static void install_crash_handler(const char *game_dir) {
    snprintf(g_crash_path, sizeof(g_crash_path), "%s/native_crash.txt", game_dir);
    char logp[512];
    snprintf(logp, sizeof(logp), "%s/engine_log.txt", game_dir);
    if (g_engine_log_fd < 0) {
        g_engine_log_fd = open(logp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    }
    static bool installed = false;
    if (installed) return;
    installed = true;
    static const int sigs[] = {SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGABRT};
    for (size_t i = 0; i < sizeof(sigs) / sizeof(sigs[0]); i++) {
        struct sigaction sa;
        memset(&sa, 0, sizeof(sa));
        sa.sa_sigaction = crash_handler;
        sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
        sigemptyset(&sa.sa_mask);
        sigaction(sigs[i], &sa, &g_prev_sa[sigs[i]]);
    }
}

static void *stdio_forwarder(void *arg) {
    (void)arg;
    char buf[1024];
    ssize_t n;
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
    setvbuf(stdout, NULL, _IOLBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    if (dup2(g_stdio_pipe[1], STDOUT_FILENO) < 0 || dup2(g_stdio_pipe[1], STDERR_FILENO) < 0) return;
    pthread_t t;
    if (pthread_create(&t, NULL, stdio_forwarder, NULL) == 0) pthread_detach(t);
}

/* ---- External Engine C-ABI Host Symbols (both mc_android_* & tc_android_*) ---- */
static void egl_drop_surface(GlState *g) {
    if (g->dpy == EGL_NO_DISPLAY || g->surf == EGL_NO_SURFACE) return;
    if (g->eglMakeCurrent) g->eglMakeCurrent(g->dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    if (g->eglDestroySurface) g->eglDestroySurface(g->dpy, g->surf);
    g->surf = EGL_NO_SURFACE;
}

static bool egl_rebind_surface(GlState *g, ANativeWindow *win) {
    if (!g->gl_ready || g->ctx == EGL_NO_CONTEXT || g->dpy == EGL_NO_DISPLAY) return false;
    egl_drop_surface(g);
    g->surf = g->eglCreateWindowSurface(g->dpy, g->cfg, win, NULL);
    if (g->surf == EGL_NO_SURFACE) return false;
    if (!g->eglMakeCurrent(g->dpy, g->surf, g->surf, g->ctx)) {
        g->eglDestroySurface(g->dpy, g->surf);
        g->surf = EGL_NO_SURFACE;
        return false;
    }
    if (g->eglSwapInterval) g->eglSwapInterval(g->dpy, g_power_profile == 0 ? 0 : 1);
    return true;
}

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
    if (!win) return 0;
    if (!load_gl_procs(&g_ext_gl)) return 0;
    if (!g_ext_gl.gl_ready) {
        if (!init_egl_surface(&g_ext_gl, win)) { destroy_egl(&g_ext_gl); return 0; }
        if (g_ext_gl.glDisable) { g_ext_gl.glDisable(GL_DEPTH_TEST); g_ext_gl.glDisable(GL_CULL_FACE); }
        if (g_ext_gl.glUseProgram) g_ext_gl.glUseProgram(0);
    } else if (g_ext_gl.eglMakeCurrent) {
        g_ext_gl.eglMakeCurrent(g_ext_gl.dpy, g_ext_gl.surf, g_ext_gl.surf, g_ext_gl.ctx);
    }
    return 1;
}

__attribute__((visibility("default"))) int32_t mc_android_init_egl(int32_t *out_w, int32_t *out_h) {
    return tc_android_init_egl(out_w, out_h);
}

__attribute__((visibility("default"))) void tc_android_swap_buffers(void) {
    pthread_mutex_lock(&g_lock);
    bool dirty = g_surface_dirty;
    ANativeWindow *pending = g_pending_window;
    if (dirty) {
        g_surface_dirty = false;
        if (g_window) ANativeWindow_release(g_window);
        g_window = pending; g_pending_window = NULL;
    }
    ANativeWindow *cur_win = g_window;
    pthread_mutex_unlock(&g_lock);
    if (dirty && g_ext_gl.lib_egl) {
        if (!cur_win) egl_drop_surface(&g_ext_gl);
        else if (!egl_rebind_surface(&g_ext_gl, cur_win)) {
            destroy_egl(&g_ext_gl);
            load_gl_procs(&g_ext_gl);
            init_egl_surface(&g_ext_gl, cur_win);
        }
    }
    if (g_ext_gl.gl_ready && g_ext_gl.eglSwapBuffers && g_ext_gl.dpy != EGL_NO_DISPLAY && g_ext_gl.surf != EGL_NO_SURFACE) {
        g_ext_gl.eglSwapBuffers(g_ext_gl.dpy, g_ext_gl.surf);
        __atomic_add_fetch(&g_swap_count, 1, __ATOMIC_RELAXED);
        EGLint qw = 0, qh = 0;
        if (g_ext_gl.eglQuerySurface &&
            g_ext_gl.eglQuerySurface(g_ext_gl.dpy, g_ext_gl.surf, EGL_WIDTH, &qw) &&
            g_ext_gl.eglQuerySurface(g_ext_gl.dpy, g_ext_gl.surf, EGL_HEIGHT, &qh) && qw > 0 && qh > 0) {
            pthread_mutex_lock(&g_lock);
            g_win_w = qw; g_win_h = qh; g_egl_w = qw; g_egl_h = qh;
            pthread_mutex_unlock(&g_lock);
        }
    }
}

__attribute__((visibility("default"))) void mc_android_swap_buffers(void) {
    tc_android_swap_buffers();
}

__attribute__((visibility("default"))) void tc_android_poll_snapshot(MuffinPollSnapshot *out) {
    if (!out) return;
    memset(out, 0, sizeof(*out));
    pthread_mutex_lock(&g_lock);
    out->width = g_win_w > 0 ? g_win_w : 1280;
    out->height = g_win_h > 0 ? g_win_h : 720;
    float mx = g_tc_input.move_x, my = g_tc_input.move_y;
    out->key_w = g_tc_input.key_w || (my > 0.28f);
    out->key_s = g_tc_input.key_s || (my < -0.28f);
    out->key_a = g_tc_input.key_a || (mx < -0.28f);
    out->key_d = g_tc_input.key_d || (mx > 0.28f);
    out->key_jump = g_tc_input.key_jump; out->key_sneak = g_tc_input.key_sneak;
    out->key_sprint = g_tc_input.key_sprint; out->key_lmb = g_tc_input.key_lmb;
    out->key_rmb = g_tc_input.key_rmb; out->key_esc = g_tc_input.key_esc;
    out->key_e = g_tc_input.key_e; out->key_q = g_tc_input.key_q;
    out->key_f = g_tc_input.key_f; out->key_t = g_tc_input.key_t;
    out->key_f3 = g_tc_input.key_f3; out->key_f5 = g_tc_input.key_f5;
    out->key_tab = g_tc_input.key_tab; out->third_person = g_tc_input.third_person;
    out->cursor_visible = g_tc_input.cursor_visible; out->hotbar_slot = g_tc_input.hotbar_slot;
    out->look_dx = g_tc_input.look_dx; out->look_dy = g_tc_input.look_dy;
    out->cursor_x = g_tc_input.cursor_x; out->cursor_y = g_tc_input.cursor_y;
    g_tc_input.look_dx = 0.0f; g_tc_input.look_dy = 0.0f;
    pthread_mutex_unlock(&g_lock);
}

__attribute__((visibility("default"))) void mc_android_poll_snapshot(MuffinPollSnapshot *out) {
    tc_android_poll_snapshot(out);
}

__attribute__((visibility("default"))) int32_t tc_android_has_window(void) {
    pthread_mutex_lock(&g_lock);
    int32_t has = (g_window != NULL || g_pending_window != NULL) ? 1 : 0;
    pthread_mutex_unlock(&g_lock);
    return has;
}

__attribute__((visibility("default"))) void tc_android_get_size(int32_t *out_w, int32_t *out_h) {
    pthread_mutex_lock(&g_lock);
    if (out_w) *out_w = g_win_w > 0 ? g_win_w : 1280;
    if (out_h) *out_h = g_win_h > 0 ? g_win_h : 720;
    pthread_mutex_unlock(&g_lock);
}

static void *ext_launch_worker(void *arg) {
    (void)arg;
    pthread_mutex_lock(&g_lock);
    PFN_engine_main fn = g_ext_entry_fn;
    char user_copy[64];
    snprintf(user_copy, sizeof(user_copy), "%s", g_username);
    pthread_mutex_unlock(&g_lock);
    if (!fn) return NULL;
    int rc = fn(user_copy, getenv("MC_GAME_DIR"), getenv("MUFFIN_RESOURCES"), &g_tc_input);
    if (rc != 0) {
        char err[256];
        snprintf(err, sizeof(err), "HATA: Harici motor cikis kodu %d dondu", rc);
        set_log(err);
    } else {
        set_log("Harici Muffin/J2ZIG motor thread'i basariyla baslatildi.");
    }
    return NULL;
}

/* ---- JNI Entry Points for com.muffin.launcher.NativeBridge ---- */
JNIEXPORT jstring JNICALL Java_com_muffin_launcher_NativeBridge_nativeProbeHardware(JNIEnv *env, jclass cls) {
    (void)cls;
    char vk_info[128] = "Vulkan: Yok";
    void *hvk = dlopen("libvulkan.so", RTLD_NOW);
    if (hvk) {
        typedef int (*PFN_vkEnumVer)(uint32_t *);
        PFN_vkEnumVer ev = (PFN_vkEnumVer)dlsym(hvk, "vkEnumerateInstanceVersion");
        uint32_t ver = (1u << 22);
        if (ev) ev(&ver);
        snprintf(vk_info, sizeof(vk_info), "Vulkan %u.%u", (ver >> 22) & 0x7fu, (ver >> 12) & 0x3ffu);
    }
    char egl_info[128] = "GLES3: Yok";
    if (dlopen("libEGL.so", RTLD_NOW) && dlopen("libGLESv3.so", RTLD_NOW)) {
        snprintf(egl_info, sizeof(egl_info), "EGL + GLES 3.x Hazir");
    }
    long cpus = sysconf(_SC_NPROCESSORS_ONLN);
    long pages = sysconf(_SC_PHYS_PAGES);
    long pg_sz = sysconf(_SC_PAGESIZE);
    double ram_gb = (pages > 0 && pg_sz > 0) ? ((double)pages * (double)pg_sz) / (1024.0 * 1024.0 * 1024.0) : 0.0;
#if defined(__aarch64__)
    const char *abi = "arm64-v8a";
#else
    const char *abi = "x86_64";
#endif
    char out[512];
    snprintf(out, sizeof(out), "Mojo 1.2 (%s) | %s | %s | %ld Cekirdek | %.1f GB RAM",
             abi, vk_info, egl_info, cpus > 0 ? cpus : 1, ram_gb);
    return (*env)->NewStringUTF(env, out);
}

JNIEXPORT jint JNICALL Java_com_muffin_launcher_NativeBridge_nativeInitEngine(
    JNIEnv *env, jclass cls,
    jstring username, jstring extLibPath, jstring gameDir, jstring resourcesDir, jstring assetsDir,
    jint backendPref, jint powerProfile, jint renderDist, jint targetFps, jfloat fovDeg) {
    (void)cls;
    pthread_once(&g_stdio_once, stdio_redirect_once);
    pthread_mutex_lock(&g_lock);
    const char *u = username ? (*env)->GetStringUTFChars(env, username, NULL) : NULL;
    if (u) { snprintf(g_username, sizeof(g_username), "%s", u); setenv("MC_USERNAME", u, 1); (*env)->ReleaseStringUTFChars(env, username, u); }
    const char *gd = gameDir ? (*env)->GetStringUTFChars(env, gameDir, NULL) : NULL;
    if (gd) { setenv("MC_GAME_DIR", gd, 1); (void)chdir(gd); install_crash_handler(gd); (*env)->ReleaseStringUTFChars(env, gameDir, gd); }
    const char *rd = resourcesDir ? (*env)->GetStringUTFChars(env, resourcesDir, NULL) : NULL;
    if (rd) { setenv("MUFFIN_RESOURCES", rd, 1); setenv("J2Z_RESOURCES", rd, 1); (*env)->ReleaseStringUTFChars(env, resourcesDir, rd); }
    const char *ad = assetsDir ? (*env)->GetStringUTFChars(env, assetsDir, NULL) : NULL;
    if (ad) { setenv("MC_ASSETS_DIR", ad, 1); (*env)->ReleaseStringUTFChars(env, assetsDir, ad); }

    setenv("MUFFIN_RHI", "1", 1);
    setenv("J2Z_RHI", "1", 1);
    setenv("J2Z_NO_LWJGL", "1", 1);
    setenv("MUFFIN_RHI_BACKEND", backendPref == 1 ? "vulkan" : "gles3", 1);
    setenv("J2Z_RHI_BACKEND", backendPref == 1 ? "vulkan" : "headless", 1);

    const char *lp = extLibPath ? (*env)->GetStringUTFChars(env, extLibPath, NULL) : NULL;
    bool ext_requested = (lp && lp[0] != '\0');
    char err_msg[512] = "";
    if (ext_requested && g_ext_handle && g_ext_launched) {
        if (strcmp(g_ext_lib_path, lp) != 0) {
            snprintf(err_msg, sizeof(err_msg), "Harici motor (%s) zaten calisiyor; degistirmek icin yeniden baslatin.", g_ext_lib_path);
            (*env)->ReleaseStringUTFChars(env, extLibPath, lp);
            pthread_mutex_unlock(&g_lock);
            set_log(err_msg);
            return -1;
        }
        (*env)->ReleaseStringUTFChars(env, extLibPath, lp);
        lp = NULL;
    } else {
        g_ext_loaded = false; g_ext_launched = false; g_ext_entry_fn = NULL;
        if (g_ext_handle) { dlclose(g_ext_handle); g_ext_handle = NULL; }
    }
    if (lp && ext_requested) {
        snprintf(g_ext_lib_path, sizeof(g_ext_lib_path), "%s", lp);
        dlerror();
        g_ext_handle = dlopen(lp, RTLD_NOW | RTLD_LOCAL);
        if (!g_ext_handle) {
            snprintf(err_msg, sizeof(err_msg), "dlopen('%s') basarisiz: %s", lp, dlerror());
        } else {
            dlerror();
            g_ext_entry_fn = (PFN_engine_main)dlsym(g_ext_handle, "muffin_android_entry");
            if (!g_ext_entry_fn) g_ext_entry_fn = (PFN_engine_main)dlsym(g_ext_handle, "j2z_android_entry");
            if (!g_ext_entry_fn) {
                snprintf(err_msg, sizeof(err_msg), "muffin_android_entry / j2z_android_entry sembolu bulunamadi.");
                dlclose(g_ext_handle); g_ext_handle = NULL;
            } else {
                g_ext_loaded = true;
            }
        }
    }
    if (lp) (*env)->ReleaseStringUTFChars(env, extLibPath, lp);
    if (ext_requested && !g_ext_loaded) {
        pthread_mutex_unlock(&g_lock); set_log(err_msg); return -1;
    }

    g_backend_pref = backendPref; g_power_profile = powerProfile;
    memset(&g_tc_input, 0, sizeof(g_tc_input));
    g_tc_input.sensitivity = 1.0f; g_tc_input.fov_deg = fovDeg;
    g_tc_input.render_dist = renderDist; g_tc_input.target_fps = targetFps;

    if (!g_ext_loaded) {
        init_sandbox_world();
        if (!g_thread_running) {
            g_stop_req = false;
            pthread_attr_t attr; pthread_attr_init(&attr);
            pthread_attr_setstacksize(&attr, 4 * 1024 * 1024);
            g_thread_running = (pthread_create(&g_render_thread, &attr, render_loop, NULL) == 0);
            pthread_attr_destroy(&attr);
        }
    }
    pthread_mutex_unlock(&g_lock);
    return g_ext_loaded ? 1 : 0;
}

JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativeAttachSurface(JNIEnv *env, jclass cls, jobject surf) {
    (void)cls;
    ANativeWindow *w = surf ? ANativeWindow_fromSurface(env, surf) : NULL;
    bool launch_ext = false;
    pthread_mutex_lock(&g_lock);
    if (g_pending_window) ANativeWindow_release(g_pending_window);
    g_pending_window = w;
    if (w) { g_win_w = ANativeWindow_getWidth(w); g_win_h = ANativeWindow_getHeight(w); }
    g_surface_dirty = true;
    if (w && g_ext_loaded && !g_ext_launched && g_ext_entry_fn) {
        g_ext_launched = true; launch_ext = true;
    }
    pthread_mutex_unlock(&g_lock);
    if (launch_ext) {
        pthread_t lt; pthread_attr_t attr; pthread_attr_init(&attr);
        pthread_attr_setstacksize(&attr, 2 * 1024 * 1024);
        if (pthread_create(&lt, &attr, ext_launch_worker, NULL) == 0) pthread_detach(lt);
        pthread_attr_destroy(&attr);
    }
}

JNIEXPORT jintArray JNICALL Java_com_muffin_launcher_NativeBridge_nativeSurfaceSizes(JNIEnv *env, jclass cls) {
    (void)cls;
    jint v[5];
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
    pthread_mutex_lock(&g_lock);
    if (g_ext_gl.lib_egl) destroy_egl(&g_ext_gl);
    if (g_ext_handle && !g_ext_launched) { dlclose(g_ext_handle); g_ext_handle = NULL; g_ext_loaded = false; g_ext_entry_fn = NULL; }
    pthread_mutex_unlock(&g_lock);
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

JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativeSetCursorMode(JNIEnv *env, jclass cls, jboolean visible, jfloat normX, jfloat normY) {
    (void)env; (void)cls;
    g_tc_input.cursor_visible = visible ? 1 : 0; g_tc_input.cursor_x = normX; g_tc_input.cursor_y = normY;
}

JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativeUpdateRuntimeConfig(
    JNIEnv *env, jclass cls, jfloat sens, jboolean invY, jfloat fov, jint rdist, jint fps) {
    (void)env; (void)cls;
    g_tc_input.sensitivity = sens; g_tc_input.invert_y = invY ? 1 : 0;
    g_tc_input.fov_deg = fov; g_tc_input.render_dist = rdist; g_tc_input.target_fps = fps;
}

JNIEXPORT void JNICALL Java_com_muffin_launcher_NativeBridge_nativePollTelemetry(JNIEnv *env, jclass cls, jlongArray outArr) {
    (void)cls;
    if (!outArr || (*env)->GetArrayLength(env, outArr) < 16) return;
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
    (void)cls;
    pthread_mutex_lock(&g_lock);
    jstring s = (*env)->NewStringUTF(env, g_last_log);
    pthread_mutex_unlock(&g_lock);
    return s;
}
