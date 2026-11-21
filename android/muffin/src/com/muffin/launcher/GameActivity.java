package com.muffin.launcher;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.DisplayMetrics;
import android.view.KeyEvent;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.view.inputmethod.InputMethodManager;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.Toast;

import java.util.List;

public final class GameActivity extends Activity implements SurfaceHolder.Callback {
    private LauncherConfig config;
    private ControlProfile activeProfile;
    private SurfaceView surfaceView;
    private TouchOverlayView overlayView;
    private EditText hiddenImeInput;
    private SoundEngine soundEngine;
    private boolean engineInitialized = false;
    private int initStatus = 0;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final long[] telemetryBuf = new long[16];
    private final Runnable telemetryTick = new Runnable() {
        @Override public void run() {
            if (!isFinishing() && NativeBridge.isLoaded()) {
                try {
                    NativeBridge.nativePollTelemetry(telemetryBuf);
                    if (overlayView != null) overlayView.updateTelemetry(telemetryBuf);
                    if (soundEngine != null) {
                        for (int i = 0; i < 6; i++) {
                            int ev = NativeBridge.nativePollSoundEvent();
                            if (ev == 0) break;
                            if ((ev & 0x10000) != 0) soundEngine.playPlace();
                            else if ((ev & 0xFF) > 0) soundEngine.playBreak(ev & 0xFF);
                        }
                    }
                } catch (Throwable ignored) {}
                mainHandler.postDelayed(this, 100);
            }
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        LauncherConfig.installCrashHandler(this);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        useWholeScreen();
        config = new LauncherConfig(this);
        activeProfile = config.loadActiveProfile();

        if (config.shizukuRam || config.debugMode) {
            final String gd = config.gameDir;
            new Thread(new Runnable() { @Override public void run() { ShizukuRam.apply(gd); } }, "shizuku-ram").start();
        }

        if (!NativeBridge.isLoaded()) {
            showFatalInitDialog("Mojo Native Motoru Yüklenemedi",
                    "libmuffin_core.so yüklenirken hata oluştu:\n" + NativeBridge.getLoadError());
            return;
        }

        try {
            EngineResources.exportEnv(this, config);
        } catch (Exception e) {
            showFatalInitDialog("Ortam Hazırlanamadı", String.valueOf(e.getMessage()));
            return;
        }

        try {
            initStatus = NativeBridge.nativeInitEngine(
                    config.username, "", config.gameDir,
                    config.resourcesDir, config.assetsDir, config.backendMode,
                    config.powerProfile, config.renderDistance, config.targetFps, config.fovDeg);
            engineInitialized = true;
        } catch (Throwable t) {
            showFatalInitDialog("Motor Başlatma Hatası", t.getClass().getSimpleName() + ": " + t.getMessage());
            return;
        }

        if (initStatus < 0) {
            String detail = NativeBridge.safeGetLastLog();
            NativeBridge.nativeShutdown();
            engineInitialized = false;
            showFatalInitDialog("Mojo Motoru Başlatılamadı",
                    "Hata kodu: " + initStatus + "\nDetay: " + detail);
            return;
        }

        NativeBridge.nativeUpdateRuntimeConfig(
                config.sensitivity, config.invertY, config.fovDeg, config.renderDistance, config.targetFps);

        final String resDir = config.resourcesDir;
        new Thread(new Runnable() {
            @Override public void run() {
                try {
                    byte[] rgba = BlockAtlas.buildAtlasRgba(resDir);
                    if (rgba != null && engineInitialized && NativeBridge.isLoaded()) {
                        NativeBridge.nativeUploadBlockAtlas(rgba, BlockAtlas.ATLAS_PX, BlockAtlas.ATLAS_PX);
                    }
                } catch (Throwable ignored) {}
            }
        }, "muffin-atlas-loader").start();

        soundEngine = new SoundEngine();
        soundEngine.initAsync(config.assetsDir);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(0xFF000000);
        hiddenImeInput = new EditText(this);
        hiddenImeInput.setAlpha(0f);
        hiddenImeInput.setBackground(null);
        root.addView(hiddenImeInput, new FrameLayout.LayoutParams(1, 1));

        surfaceView = new SurfaceView(this);
        SurfaceHolder holder = surfaceView.getHolder();
        holder.addCallback(this);
        if (config.resScalePct > 0 && config.resScalePct < 100) {
            int[] real = realScreenSize();
            int rawW = Math.max(real[0], real[1]), rawH = Math.min(real[0], real[1]);
            holder.setFixedSize(Math.max(640, (rawW * config.resScalePct) / 100),
                    Math.max(360, (rawH * config.resScalePct) / 100));
        }

        overlayView = new TouchOverlayView(this, config, activeProfile);
        String verTag = (config.selectedVersion != null && !config.selectedVersion.isEmpty())
                ? config.selectedVersion : "Mojo 1.2";
        overlayView.setEngineModeLabel("Mojo 1.2 Chunk Engine • " + verTag);
        overlayView.setHostCallbacks(new TouchOverlayView.HostCallbacks() {
            @Override public void onOpenQuickMenu() { showQuickMenu(); }
            @Override public void onToggleSoftKeyboard() { toggleKeyboard(); }
        });

        root.addView(surfaceView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        root.addView(overlayView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
    }

    private void showFatalInitDialog(String title, String message) {
        FrameLayout empty = new FrameLayout(this);
        empty.setBackgroundColor(0xFF070B12);
        setContentView(empty);
        new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle(title).setMessage(message).setCancelable(false)
                .setPositiveButton("Başlatıcıya Dön", new DialogInterface.OnClickListener() {
                    @Override public void onClick(DialogInterface dialog, int which) { finish(); }
                }).show();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) enterFullscreenSafe();
    }

    private void useWholeScreen() {
        if (Build.VERSION.SDK_INT >= 28) {
            WindowManager.LayoutParams lp = getWindow().getAttributes();
            lp.layoutInDisplayCutoutMode = Build.VERSION.SDK_INT >= 30
                    ? WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
                    : WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
            getWindow().setAttributes(lp);
        }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS);
        if (Build.VERSION.SDK_INT >= 30) getWindow().setDecorFitsSystemWindows(false);
        enterFullscreenSafe();
    }

    private int[] realScreenSize() {
        if (Build.VERSION.SDK_INT >= 30) {
            android.graphics.Rect b = getWindowManager().getCurrentWindowMetrics().getBounds();
            return new int[] {b.width(), b.height()};
        }
        DisplayMetrics dm = new DisplayMetrics();
        getWindowManager().getDefaultDisplay().getRealMetrics(dm);
        return new int[] {dm.widthPixels, dm.heightPixels};
    }

    private void enterFullscreenSafe() {
        try {
            if (Build.VERSION.SDK_INT >= 30) {
                WindowInsetsController c = getWindow().getInsetsController();
                if (c != null) {
                    c.hide(WindowInsets.Type.statusBars() | WindowInsets.Type.navigationBars());
                    c.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
                }
            }
        } catch (Throwable ignored) {}
    }

    private void toggleKeyboard() {
        if (hiddenImeInput == null) return;
        hiddenImeInput.requestFocus();
        InputMethodManager imm = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
        if (imm != null) imm.toggleSoftInput(InputMethodManager.SHOW_FORCED, 0);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (!engineInitialized) return;
        config.load();
        activeProfile = config.loadActiveProfile();
        if (overlayView != null) overlayView.setProfile(activeProfile);
        NativeBridge.nativeUpdateRuntimeConfig(
                config.sensitivity, config.invertY, config.fovDeg, config.renderDistance, config.targetFps);
        mainHandler.removeCallbacks(telemetryTick);
        mainHandler.postDelayed(telemetryTick, 200);
    }

    @Override
    protected void onPause() {
        mainHandler.removeCallbacks(telemetryTick);
        if (overlayView != null) overlayView.releaseAllInputs();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        mainHandler.removeCallbacks(telemetryTick);
        if (soundEngine != null) {
            soundEngine.release();
            soundEngine = null;
        }
        if (engineInitialized && NativeBridge.isLoaded()) {
            NativeBridge.nativeShutdown();
            engineInitialized = false;
        }
        super.onDestroy();
    }

    @Override public void surfaceCreated(SurfaceHolder holder) {
        if (engineInitialized && NativeBridge.isLoaded()) NativeBridge.nativeAttachSurface(holder.getSurface());
    }

    @Override public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) {
        if (engineInitialized && NativeBridge.isLoaded()) NativeBridge.nativeResizeSurface(width, height);
    }

    @Override public void surfaceDestroyed(SurfaceHolder holder) {
        if (overlayView != null) overlayView.releaseAllInputs();
        if (engineInitialized && NativeBridge.isLoaded()) NativeBridge.nativeDetachSurface();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) { showQuickMenu(); return true; }
        if (engineInitialized && NativeBridge.isLoaded()) {
            int mapped = mapKey(keyCode);
            if (mapped != 0) {
                NativeBridge.nativeSetKey(mapped, true);
                NativeBridge.nativeSetKey(mapped, false);
                return true;
            }
        }
        return super.onKeyDown(keyCode, event);
    }

    private static int mapKey(int kc) {
        if (kc >= KeyEvent.KEYCODE_A && kc <= KeyEvent.KEYCODE_Z) return 'A' + (kc - KeyEvent.KEYCODE_A);
        if (kc >= KeyEvent.KEYCODE_0 && kc <= KeyEvent.KEYCODE_9) return '0' + (kc - KeyEvent.KEYCODE_0);
        if (kc == KeyEvent.KEYCODE_SPACE) return GlfwKeys.KEY_SPACE;
        if (kc == KeyEvent.KEYCODE_ENTER) return GlfwKeys.KEY_ENTER;
        if (kc == KeyEvent.KEYCODE_DEL) return GlfwKeys.KEY_BACKSPACE;
        if (kc == KeyEvent.KEYCODE_ESCAPE) return GlfwKeys.KEY_ESCAPE;
        if (kc == KeyEvent.KEYCODE_TAB) return GlfwKeys.KEY_TAB;
        return 0;
    }

    private void showQuickMenu() {
        if (overlayView != null) overlayView.releaseAllInputs();
        String modeStr = "Mojo 1.2 Chunk Engine";
        String[] items = new String[] {
            (overlayView != null && overlayView.isShowF3Hud()) ? "F3 Mojo Telemetry Gizle" : "F3 Mojo Telemetry Göster",
            "Kontrol Profilini Değiştir (" + config.activeProfileName + ")",
            "Tuş Düzeni Editörünü Aç",
            "Sohbet / Komut Gönder",
            "Native Motor Günlüğü",
            "Başlatıcıya Dön (Çıkış)"
        };
        new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle("Muffin Mojo — " + config.username + " • " + modeStr)
                .setItems(items, new DialogInterface.OnClickListener() {
                    @Override public void onClick(DialogInterface dialog, int which) {
                        if (which == 0 && overlayView != null) overlayView.toggleF3Hud();
                        else if (which == 1) showProfileSwitcher();
                        else if (which == 2) {
                            Intent i = new Intent(GameActivity.this, ControlsEditorActivity.class);
                            i.putExtra("profile_name", config.activeProfileName);
                            startActivity(i);
                        } else if (which == 3) showQuickCommandDialog();
                        else if (which == 4) {
                            new AlertDialog.Builder(GameActivity.this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                                    .setTitle("Mojo Native Log").setMessage(NativeBridge.safeGetLastLog())
                                    .setPositiveButton("Tamam", null).show();
                        } else if (which == 5) finish();
                    }
                }).setNegativeButton("Devam Et", null).show();
    }

    private void showProfileSwitcher() {
        List<String> names = config.listProfileNames();
        final String[] arr = names.toArray(new String[0]);
        new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle("Kontrol Profili Seç").setItems(arr, new DialogInterface.OnClickListener() {
                    @Override public void onClick(DialogInterface dialog, int which) {
                        config.activeProfileName = arr[which]; config.save();
                        activeProfile = config.loadActiveProfile();
                        if (overlayView != null) overlayView.setProfile(activeProfile);
                        Toast.makeText(GameActivity.this, "Profil: " + arr[which], Toast.LENGTH_SHORT).show();
                    }
                }).show();
    }

    private void showQuickCommandDialog() {
        final EditText input = new EditText(this);
        input.setHint("Ör: /gamemode creative");
        new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle("Komut Gönder").setView(input)
                .setPositiveButton("Gönder", new DialogInterface.OnClickListener() {
                    @Override public void onClick(DialogInterface dialog, int which) {
                        if (!NativeBridge.isLoaded()) return;
                        NativeBridge.nativeSetKey(GlfwKeys.KEY_T, true); NativeBridge.nativeSetKey(GlfwKeys.KEY_T, false);
                        String msg = input.getText().toString();
                        for (int i = 0; i < msg.length(); i++) {
                            char ch = Character.toUpperCase(msg.charAt(i));
                            if ((ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9')) {
                                NativeBridge.nativeSetKey(ch, true); NativeBridge.nativeSetKey(ch, false);
                            } else if (ch == ' ') {
                                NativeBridge.nativeSetKey(GlfwKeys.KEY_SPACE, true); NativeBridge.nativeSetKey(GlfwKeys.KEY_SPACE, false);
                            }
                        }
                        NativeBridge.nativeSetKey(GlfwKeys.KEY_ENTER, true); NativeBridge.nativeSetKey(GlfwKeys.KEY_ENTER, false);
                    }
                }).setNegativeButton("İptal", null).show();
    }
}
