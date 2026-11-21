package com.muffin.launcher;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.PrintWriter;
import java.io.RandomAccessFile;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

public final class LauncherConfig {
    private static final String PREF_NAME = "muffin_mojo_prefs_v1";
    private static final String KEY_USERNAME = "username";
    private static final String KEY_RECENT_USERS = "recent_users";
    private static final String KEY_ACTIVE_PROFILE = "active_profile";
    private static final String KEY_PROFILES_JSON = "profiles_map_json";

    private static final String KEY_VERSION_NAME = "version_name";
    private static final String KEY_CLIENT_JAR = "client_jar_path";
    private static final String KEY_COMPLETE_STAMP = "complete_version_stamp";
    private static final String KEY_BACKEND_MODE = "backend_mode";
    private static final String KEY_POWER_PROFILE = "power_profile";
    private static final String KEY_RENDER_DIST = "render_dist";
    private static final String KEY_TARGET_FPS = "target_fps";
    private static final String KEY_RES_SCALE = "res_scale";
    private static final String KEY_MEM_LIMIT = "mem_limit_pct";
    private static final String KEY_SHIZUKU_RAM = "shizuku_ram";
    private static final String KEY_DEBUG_MODE = "debug_mode";
    private static final String KEY_SHOW_SNAPSHOTS = "show_snapshots";
    private static final String KEY_FOV_DEG = "fov_deg";
    private static final String KEY_SENSITIVITY = "sensitivity";
    private static final String KEY_INVERT_Y = "invert_y";
    private static final String KEY_HAPTIC = "haptic_level";
    private static final String KEY_AUTO_F3 = "auto_f3";
    private static final String KEY_GAME_DIR = "game_dir";
    private static final String KEY_EXT_LIB = "ext_lib_path";
    private static final String KEY_EXT_LIB_URL = "ext_lib_url";
    private static final String KEY_RES_DIR = "resources_dir";
    private static final String KEY_ASSETS_DIR = "assets_dir";
    private static final String KEY_EXTRA_ENV = "extra_env";

    public static final int BACKEND_AUTO = 0;
    public static final int BACKEND_VULKAN = 1;
    public static final int BACKEND_GLES = 2;

    public static final int POWER_ECO = 0;
    public static final int POWER_BALANCED = 1;
    public static final int POWER_TURBO = 2;

    private final Context appContext;
    private final SharedPreferences prefs;
    public final File defaultRootDir;

    public String username;
    public final List<String> recentUsernames = new ArrayList<String>();
    public String activeProfileName;
    public String selectedVersion;
    public String clientJarPath;
    public String completeVersionStamp;

    public int backendMode;
    public int powerProfile;
    public int renderDistance;
    public int targetFps;
    public int resScalePct;
    public int memLimitPct;
    public boolean shizukuRam;
    public boolean debugMode;
    public boolean showSnapshots;
    public float fovDeg;
    public float sensitivity;
    public boolean invertY;
    public int hapticLevel;
    public boolean autoF3;

    public String gameDir;
    public String extLibPath;
    public String extLibUrl;
    public String resourcesDir;
    public String assetsDir;
    public String extraEnv;

    public LauncherConfig(Context ctx) {
        this.appContext = ctx.getApplicationContext();
        this.prefs = appContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE);
        this.defaultRootDir = new File(appContext.getFilesDir(), "muffin");
        ensureDirectories();
        load();
    }

    public void ensureDirectories() {
        new File(defaultRootDir, "game").mkdirs();
        new File(defaultRootDir, "resources").mkdirs();
        new File(defaultRootDir, "assets").mkdirs();
        new File(defaultRootDir, "libraries").mkdirs();
        new File(defaultRootDir, "native").mkdirs();
        new File(defaultRootDir, "versions").mkdirs();
    }

    public File versionsRoot() {
        return new File(defaultRootDir, "versions");
    }

    public File librariesRoot() {
        return new File(defaultRootDir, "libraries");
    }

    public void load() {
        username = prefs.getString(KEY_USERNAME, "MojoOyuncu");
        recentUsernames.clear();
        try {
            JSONArray arr = new JSONArray(prefs.getString(KEY_RECENT_USERS, "[]"));
            for (int i = 0; i < arr.length(); i++) {
                String u = arr.optString(i, "").trim();
                if (!u.isEmpty() && !recentUsernames.contains(u)) recentUsernames.add(u);
            }
        } catch (Exception ignored) {}
        if (recentUsernames.isEmpty()) recentUsernames.add(username);

        selectedVersion = prefs.getString(KEY_VERSION_NAME, "Son Resmî Sürüm (Otomatik)");
        clientJarPath = prefs.getString(KEY_CLIENT_JAR, "");
        completeVersionStamp = prefs.getString(KEY_COMPLETE_STAMP, "");
        activeProfileName = prefs.getString(KEY_ACTIVE_PROFILE, ControlProfile.PRESET_DEFAULT);
        backendMode = prefs.getInt(KEY_BACKEND_MODE, BACKEND_AUTO);
        powerProfile = prefs.getInt(KEY_POWER_PROFILE, POWER_BALANCED);
        renderDistance = prefs.getInt(KEY_RENDER_DIST, 12);
        targetFps = prefs.getInt(KEY_TARGET_FPS, 60);
        resScalePct = prefs.getInt(KEY_RES_SCALE, 100);
        memLimitPct = prefs.getInt(KEY_MEM_LIMIT, 65);
        shizukuRam = prefs.getBoolean(KEY_SHIZUKU_RAM, false);
        debugMode = prefs.getBoolean(KEY_DEBUG_MODE, false);
        showSnapshots = prefs.getBoolean(KEY_SHOW_SNAPSHOTS, false);
        fovDeg = prefs.getFloat(KEY_FOV_DEG, 75f);
        sensitivity = prefs.getFloat(KEY_SENSITIVITY, 1.0f);
        invertY = prefs.getBoolean(KEY_INVERT_Y, false);
        hapticLevel = prefs.getInt(KEY_HAPTIC, 1);
        autoF3 = prefs.getBoolean(KEY_AUTO_F3, true);

        gameDir = prefs.getString(KEY_GAME_DIR, new File(defaultRootDir, "game").getAbsolutePath());
        extLibPath = prefs.getString(KEY_EXT_LIB, "");
        extLibUrl = prefs.getString(KEY_EXT_LIB_URL, "");
        resourcesDir = prefs.getString(KEY_RES_DIR, new File(defaultRootDir, "resources").getAbsolutePath());
        assetsDir = prefs.getString(KEY_ASSETS_DIR, new File(defaultRootDir, "assets").getAbsolutePath());
        extraEnv = prefs.getString(KEY_EXTRA_ENV, "");

        ensureDefaultProfilesSeeded();
    }

    public void save() {
        String cleanUser = sanitizeUsername(username);
        if (!cleanUser.isEmpty()) {
            username = cleanUser;
            recordRecentUsername(cleanUser);
        }
        JSONArray arr = new JSONArray();
        for (String u : recentUsernames) arr.put(u);

        prefs.edit()
                .putString(KEY_USERNAME, username)
                .putString(KEY_RECENT_USERS, arr.toString())
                .putString(KEY_ACTIVE_PROFILE, activeProfileName)
                .putString(KEY_VERSION_NAME, selectedVersion)
                .putString(KEY_CLIENT_JAR, clientJarPath)
                .putString(KEY_COMPLETE_STAMP, completeVersionStamp)
                .putInt(KEY_BACKEND_MODE, backendMode)
                .putInt(KEY_POWER_PROFILE, powerProfile)
                .putInt(KEY_RENDER_DIST, renderDistance)
                .putInt(KEY_TARGET_FPS, targetFps)
                .putInt(KEY_RES_SCALE, resScalePct)
                .putInt(KEY_MEM_LIMIT, memLimitPct)
                .putBoolean(KEY_SHIZUKU_RAM, shizukuRam)
                .putBoolean(KEY_DEBUG_MODE, debugMode)
                .putBoolean(KEY_SHOW_SNAPSHOTS, showSnapshots)
                .putFloat(KEY_FOV_DEG, fovDeg)
                .putFloat(KEY_SENSITIVITY, sensitivity)
                .putBoolean(KEY_INVERT_Y, invertY)
                .putInt(KEY_HAPTIC, hapticLevel)
                .putBoolean(KEY_AUTO_F3, autoF3)
                .putString(KEY_GAME_DIR, gameDir)
                .putString(KEY_EXT_LIB, extLibPath)
                .putString(KEY_EXT_LIB_URL, extLibUrl)
                .putString(KEY_RES_DIR, resourcesDir)
                .putString(KEY_ASSETS_DIR, assetsDir)
                .putString(KEY_EXTRA_ENV, extraEnv)
                .apply();
    }

    public static String sanitizeUsername(String raw) {
        if (raw == null) return "";
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < raw.length(); i++) {
            char c = raw.charAt(i);
            if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_') {
                sb.append(c);
                if (sb.length() >= 16) break;
            }
        }
        return sb.toString();
    }

    public static boolean isValidUsername(String raw) {
        if (raw == null || raw.length() < 3 || raw.length() > 16) return false;
        for (int i = 0; i < raw.length(); i++) {
            char c = raw.charAt(i);
            if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_')) {
                return false;
            }
        }
        return true;
    }

    public void recordRecentUsername(String user) {
        if (user == null || user.isEmpty()) return;
        recentUsernames.remove(user);
        recentUsernames.add(0, user);
        while (recentUsernames.size() > 8) recentUsernames.remove(recentUsernames.size() - 1);
    }

    private JSONObject readProfileMap() {
        try { return new JSONObject(prefs.getString(KEY_PROFILES_JSON, "{}")); }
        catch (Exception e) { return new JSONObject(); }
    }

    private void writeProfileMap(JSONObject map) {
        prefs.edit().putString(KEY_PROFILES_JSON, map.toString()).apply();
    }

    private void ensureDefaultProfilesSeeded() {
        JSONObject map = readProfileMap();
        boolean changed = false;
        for (String name : ControlProfile.builtInNames()) {
            if (!map.has(name)) {
                try { map.put(name, ControlProfile.createPreset(name).toJson()); changed = true; }
                catch (Exception ignored) {}
            }
        }
        if (changed) writeProfileMap(map);
    }

    public List<String> listProfileNames() {
        JSONObject map = readProfileMap();
        Set<String> ordered = new LinkedHashSet<String>();
        for (String b : ControlProfile.builtInNames()) if (map.has(b)) ordered.add(b);
        JSONArray keys = map.names();
        if (keys != null) {
            List<String> custom = new ArrayList<String>();
            for (int i = 0; i < keys.length(); i++) {
                String k = keys.optString(i, "");
                if (!k.isEmpty() && !ordered.contains(k)) custom.add(k);
            }
            Collections.sort(custom);
            ordered.addAll(custom);
        }
        return new ArrayList<String>(ordered);
    }

    public ControlProfile loadProfile(String profileName) {
        JSONObject map = readProfileMap();
        JSONObject obj = map.optJSONObject(profileName);
        if (obj != null) {
            try { return ControlProfile.fromJson(obj); } catch (Exception ignored) {}
        }
        ControlProfile fallback = ControlProfile.createPreset(profileName);
        saveProfile(fallback);
        return fallback;
    }

    public ControlProfile loadActiveProfile() {
        return loadProfile(activeProfileName);
    }

    public void saveProfile(ControlProfile profile) {
        if (profile == null || profile.name == null || profile.name.trim().isEmpty()) return;
        JSONObject map = readProfileMap();
        try { map.put(profile.name.trim(), profile.toJson()); writeProfileMap(map); }
        catch (Exception ignored) {}
    }

    public boolean deleteProfile(String profileName) {
        for (String builtin : ControlProfile.builtInNames()) {
            if (builtin.equals(profileName)) return false;
        }
        JSONObject map = readProfileMap();
        map.remove(profileName);
        writeProfileMap(map);
        if (profileName.equals(activeProfileName)) {
            activeProfileName = ControlProfile.PRESET_DEFAULT;
            save();
        }
        return true;
    }

    public ControlProfile resetProfileToDefault(String profileName) {
        ControlProfile fresh = ControlProfile.createPreset(profileName);
        fresh.name = profileName;
        saveProfile(fresh);
        return fresh;
    }

    public File getNativeImportTarget() {
        return new File(new File(defaultRootDir, "native"), "libminecraft_muffin.so");
    }

    public boolean isSetupComplete() {
        File jar = new File(clientJarPath == null ? "" : clientJarPath);
        File res = new File(resourcesDir == null ? "" : resourcesDir);
        return jar.isFile()
                && !completeVersionStamp.isEmpty()
                && new File(res, "assets").isDirectory();
    }

    public void resetAll() {
        prefs.edit().clear().apply();
        ensureDirectories();
        load();
    }

    public static void installCrashHandler(final Context ctx) {
        final Thread.UncaughtExceptionHandler prev = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler(new Thread.UncaughtExceptionHandler() {
            @Override
            public void uncaughtException(Thread t, Throwable e) {
                try {
                    StringWriter sw = new StringWriter();
                    e.printStackTrace(new PrintWriter(sw));
                    File f = new File(ctx.getFilesDir(), "last_crash.txt");
                    FileOutputStream fos = new FileOutputStream(f);
                    fos.write(("Thread: " + t.getName() + "\n" + sw).getBytes(StandardCharsets.UTF_8));
                    fos.close();
                } catch (Throwable ignored) {}
                if (prev != null) prev.uncaughtException(t, e);
            }
        });
    }

    public File newestCrashReport() {
        File[] fs = new File(gameDir == null ? "" : gameDir, "crash-reports").listFiles();
        File best = null;
        if (fs != null) for (File f : fs) {
            if (f.isFile() && f.getName().endsWith(".txt") && (best == null || f.lastModified() > best.lastModified())) best = f;
        }
        return best;
    }

    public List<File> reportFiles() {
        List<File> out = new ArrayList<File>();
        File gd = new File(gameDir == null ? "" : gameDir);
        for (String sub : new String[] {"crash-reports", "logs"}) {
            File[] fs = new File(gd, sub).listFiles();
            if (fs != null) for (File f : fs) if (f.isFile() && f.length() < 32L * 1024 * 1024) out.add(f);
        }
        for (File f : new File[] {
                new File(gd, "engine_log.txt"), new File(gd, "native_crash.txt"),
                new File(gd, "display_info.txt"), new File(gd, "memory_limit.txt"),
                new File(gd, "shizuku.txt"), new File(gd, "metrics.csv"),
                new File(appContext.getFilesDir(), "last_crash.txt")}) {
            if (f.isFile()) out.add(f);
        }
        return out;
    }

    public String readLastCrash() {
        StringBuilder sb = new StringBuilder();
        File lim = new File(gameDir == null ? "" : gameDir, "memory_limit.txt");
        if (lim.isFile()) sb.append("== Bellek siniri ==\n").append(readTail(lim, 4096)).append("\n");
        File mcr = newestCrashReport();
        if (mcr != null) sb.append("== MC cokme: ").append(mcr.getName()).append(" ==\n").append(readHead(mcr, 24 * 1024)).append("\n");
        File nat = new File(gameDir == null ? "" : gameDir, "native_crash.txt");
        if (nat.isFile()) sb.append("== Native cokme ==\n").append(readTail(nat, 16 * 1024)).append("\n");
        File f = new File(appContext.getFilesDir(), "last_crash.txt");
        if (f.isFile()) sb.append("== Java cokme ==\n").append(readTail(f, 16 * 1024)).append("\n");
        File log = new File(gameDir == null ? "" : gameDir, "engine_log.txt");
        if (log.isFile()) sb.append("== Motor gunlugu ==\n").append(readTail(log, 24 * 1024));
        return sb.length() == 0 ? "Kayitli cokme yok." : sb.toString();
    }

    private static String readHead(File f, int max) {
        try (FileInputStream in = new FileInputStream(f)) {
            byte[] buf = new byte[(int) Math.min(max, f.length())];
            int off = 0, r;
            while (off < buf.length && (r = in.read(buf, off, buf.length - off)) > 0) off += r;
            return new String(buf, 0, off, StandardCharsets.UTF_8);
        } catch (Throwable t) { return "okunamadi: " + t.getMessage(); }
    }

    private static String readTail(File f, int max) {
        try (RandomAccessFile raf = new RandomAccessFile(f, "r")) {
            long len = raf.length(), start = Math.max(0, len - max);
            byte[] buf = new byte[(int) (len - start)];
            raf.seek(start);
            raf.readFully(buf);
            return new String(buf, StandardCharsets.UTF_8);
        } catch (Throwable t) { return "okunamadi: " + t.getMessage(); }
    }
}
