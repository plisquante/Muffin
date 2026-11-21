package com.muffin.launcher;

import android.content.pm.PackageManager;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;

import rikka.shizuku.Shizuku;

final class ShizukuRam {
    static final int REQUEST_CODE = 4103;
    private static final String PKG = "com.muffin.launcher";
    private static final String[] COMMANDS = {
        "am kill-all",
        "dumpsys deviceidle whitelist +" + PKG,
        "cmd appops set " + PKG + " RUN_ANY_IN_BACKGROUND allow",
        "am set-standby-bucket " + PKG + " active",
        "settings put global settings_enable_monitor_phantom_procs false",
    };

    private ShizukuRam() {}

    static boolean running() {
        try { return Shizuku.pingBinder(); } catch (Throwable t) { return false; }
    }

    static boolean granted() {
        try { return running() && Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED; }
        catch (Throwable t) { return false; }
    }

    static void request(Shizuku.OnRequestPermissionResultListener listener) {
        Shizuku.addRequestPermissionResultListener(listener);
        Shizuku.requestPermission(REQUEST_CODE);
    }

    static String apply(String gameDir) {
        StringBuilder log = new StringBuilder("Muffin Shizuku RAM ayari\n");
        if (!granted()) {
            log.append(running() ? "Shizuku izni verilmemis\n" : "Shizuku servisi calismiyor\n");
        } else {
            for (String cmd : COMMANDS) {
                log.append("$ ").append(cmd).append('\n');
                try {
                    Process p = newProcess(new String[] {"sh", "-c", cmd});
                    String out = readAll(p.getInputStream()) + readAll(p.getErrorStream());
                    int rc = p.waitFor();
                    if (!out.isEmpty()) log.append(out.trim()).append('\n');
                    log.append("-> exit ").append(rc).append('\n');
                } catch (Throwable t) {
                    log.append("-> ERROR: ").append(t).append('\n');
                }
            }
        }
        try (FileOutputStream os = new FileOutputStream(new File(gameDir, "shizuku.txt"))) {
            os.write(log.toString().getBytes(StandardCharsets.UTF_8));
        } catch (Exception ignored) {}
        return log.toString();
    }

    private static Process newProcess(String[] cmd) throws Exception {
        Method m = Shizuku.class.getDeclaredMethod("newProcess", String[].class, String[].class, String.class);
        m.setAccessible(true);
        return (Process) m.invoke(null, cmd, null, null);
    }

    private static String readAll(InputStream in) throws Exception {
        ByteArrayOutputStream bo = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = in.read(buf)) > 0) bo.write(buf, 0, n);
        return bo.toString("UTF-8");
    }
}
