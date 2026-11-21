package com.muffin.launcher;

import android.view.Surface;

public final class NativeBridge {
    private static volatile boolean sLoaded = false;
    private static volatile String sLoadError = null;

    static {
        try {
            System.loadLibrary("muffin_core");
            sLoaded = true;
        } catch (Throwable t) {
            sLoaded = false;
            sLoadError = t.getClass().getSimpleName() + ": " + t.getMessage();
        }
    }

    private NativeBridge() {}

    public static boolean isLoaded() {
        return sLoaded;
    }

    public static String getLoadError() {
        return sLoadError;
    }

    public static String safeProbeHardware() {
        if (!sLoaded) {
            return "Native Mojo kutuphanesi yuklenemedi: " + (sLoadError != null ? sLoadError : "Bilinmeyen hata");
        }
        try {
            return nativeProbeHardware();
        } catch (Throwable t) {
            return "Probe hatasi: " + t.getMessage();
        }
    }

    public static String safeGetLastLog() {
        if (!sLoaded) {
            return "Native yukleme hatasi: " + (sLoadError != null ? sLoadError : "-");
        }
        try {
            return nativeGetLastLog();
        } catch (Throwable t) {
            return "Log okunamadi: " + t.getMessage();
        }
    }

    public static native String nativeProbeHardware();

    public static native int nativeInitEngine(
            String username,
            String extLibPath,
            String gameDir,
            String resourcesDir,
            String assetsDir,
            int backendPref,
            int powerProfile,
            int renderDist,
            int targetFps,
            float fovDeg);

    public static native void nativeAttachSurface(Surface surface);
    public static native void nativeResizeSurface(int w, int h);
    public static native int[] nativeSurfaceSizes();
    public static native void nativeDetachSurface();
    public static native void nativeShutdown();
    public static native void nativeSetKey(int glfwKey, boolean down);
    public static native void nativeSetMoveAxis(float x, float y);
    public static native void nativeAddLookDelta(float dx, float dy);
    public static native void nativeMouseButton(int button, boolean down);
    public static native void nativeSelectHotbar(int slot);
    public static native void nativeSetCursorMode(boolean cursorVisible, float normX, float normY);
    public static native void nativeUpdateRuntimeConfig(float sensitivity, boolean invertY, float fovDeg, int renderDist, int targetFps);
    public static native void nativePollTelemetry(long[] out16);
    public static native String nativeGetLastLog();
}
