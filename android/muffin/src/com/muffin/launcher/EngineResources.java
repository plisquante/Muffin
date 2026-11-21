package com.muffin.launcher;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.system.Os;

import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Enumeration;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import java.util.zip.ZipInputStream;

final class EngineResources {
    static final String BUNDLE_ASSET = "engine_resources.zip";
    private static final String STAMP = ".source";
    private static final String OVERRIDE_PREFIX = "__override__/";
    static final String JAVA_HOME_DIR = "__javahome__";
    static final long NATIVE_RESERVE_MB = 1024;

    interface Progress {
        void status(String message);
    }

    private EngineResources() {}

    static void prepare(Context ctx, LauncherConfig cfg, Progress progress) throws IOException {
        File jar = new File(cfg.clientJarPath == null ? "" : cfg.clientJarPath);
        if (!jar.isFile()) return;
        File dir = new File(cfg.resourcesDir);
        String ident = jar.length() + ":" + jar.lastModified() + ":" + bundleIdent(ctx);
        File stamp = new File(dir, STAMP);
        if (stamp.isFile() && ident.equals(readText(stamp)) && new File(dir, "assets").isDirectory()) {
            return;
        }

        if (progress != null) progress.status("5/5 Kaynak dizini hazırlanıyor...");
        deleteRecursively(dir);
        if (!dir.mkdirs() && !dir.isDirectory()) throw new IOException("Kaynak dizini oluşturulamadı: " + dir);

        if (progress != null) progress.status("5/5 client.jar içerikleri çıkarılıyor (assets, data, shaders)...");
        int fromJar = extractJar(jar, dir, progress);
        extractBundle(ctx, dir);
        if (fromJar == 0) throw new IOException("client.jar içinde kaynak dosyası bulunamadı: " + jar);
        writeText(stamp, ident);
    }

    static void exportEnv(Context ctx, LauncherConfig cfg) throws IOException {
        File home = new File(ctx.getFilesDir(), "muffin/home");
        File tmp = new File(ctx.getCacheDir(), "muffin-tmp");
        for (File d : new File[] {home, tmp, new File(cfg.gameDir), new File(cfg.assetsDir)}) {
            if (!d.isDirectory() && !d.mkdirs() && !d.isDirectory()) throw new IOException("Dizin açılamadı: " + d);
        }
        try {
            Os.setenv("MUFFIN_NATIVES", ctx.getApplicationInfo().nativeLibraryDir, true);
            Os.setenv("J2Z_NATIVES", ctx.getApplicationInfo().nativeLibraryDir, true);
            Os.setenv("MUFFIN_JAVA_HOME", new File(cfg.resourcesDir, JAVA_HOME_DIR).getAbsolutePath(), true);
            Os.setenv("J2Z_JAVA_HOME", new File(cfg.resourcesDir, JAVA_HOME_DIR).getAbsolutePath(), true);
            Os.setenv("HOME", home.getAbsolutePath(), true);
            Os.setenv("TMPDIR", tmp.getAbsolutePath(), true);
            Os.setenv("MUFFIN_LIB_DIR", cfg.librariesRoot().getAbsolutePath(), true);

            android.app.ActivityManager am = (android.app.ActivityManager) ctx.getSystemService(Context.ACTIVITY_SERVICE);
            android.app.ActivityManager.MemoryInfo mem = new android.app.ActivityManager.MemoryInfo();
            am.getMemoryInfo(mem);
            long limitMb = (mem.totalMem >> 20) * Math.max(30, Math.min(80, cfg.memLimitPct)) / 100;
            long heapMb = Math.max(512, limitMb - NATIVE_RESERVE_MB);
            Os.setenv("J2Z_HEAP_MAX_MB", Long.toString(heapMb), true);

            File jar = new File(cfg.clientJarPath == null ? "" : cfg.clientJarPath);
            File verDir = jar.getParentFile();
            if (verDir != null && jar.isFile()) {
                String id = verDir.getName();
                Os.setenv("MC_VERSION", id, true);
                File meta = new File(verDir, id + ".json");
                if (meta.isFile()) {
                    JSONObject idx = new JSONObject(readText(meta)).optJSONObject("assetIndex");
                    if (idx != null && !idx.optString("id", "").isEmpty()) {
                        Os.setenv("MC_ASSET_INDEX", idx.getString("id"), true);
                    }
                }
            }
        } catch (android.system.ErrnoException | org.json.JSONException e) {
            throw new IOException("Ortam hazırlama hatası: " + e.getMessage());
        }
    }

    private static String bundleIdent(Context ctx) {
        try {
            PackageInfo pi = ctx.getPackageManager().getPackageInfo(ctx.getPackageName(), 0);
            return pi.lastUpdateTime + "/" + pi.versionCode;
        } catch (Throwable t) { return "?"; }
    }

    private static boolean skipJarEntry(String n) {
        if (n.endsWith(".class") || n.startsWith("META-INF/versions/")) return true;
        String up = n.toUpperCase(java.util.Locale.ROOT);
        return up.startsWith("META-INF/") && (up.endsWith(".SF") || up.endsWith(".RSA") || up.endsWith(".DSA") || up.endsWith(".MF"));
    }

    private static int extractJar(File jar, File dest, Progress progress) throws IOException {
        int n = 0;
        try (ZipFile z = new ZipFile(jar)) {
            Enumeration<? extends ZipEntry> en = z.entries();
            while (en.hasMoreElements()) {
                ZipEntry e = en.nextElement();
                if (e.isDirectory() || skipJarEntry(e.getName())) continue;
                try (InputStream in = z.getInputStream(e)) { writeEntry(dest, e.getName(), in, true); }
                n++;
                if (progress != null && n % 2000 == 0) progress.status("5/5 client.jar: " + n + " dosya çıkarıldı...");
            }
        }
        return n;
    }

    private static void extractBundle(Context ctx, File dest) throws IOException {
        try (InputStream raw = ctx.getAssets().open(BUNDLE_ASSET); ZipInputStream zin = new ZipInputStream(raw)) {
            ZipEntry e;
            while ((e = zin.getNextEntry()) != null) {
                if (e.isDirectory()) continue;
                String name = e.getName();
                if (name.startsWith(OVERRIDE_PREFIX)) writeEntry(dest, name.substring(OVERRIDE_PREFIX.length()), zin, true);
                else writeEntry(dest, name, zin, false);
            }
        } catch (java.io.FileNotFoundException ignored) {}
    }

    private static void writeEntry(File root, String name, InputStream in, boolean overwrite) throws IOException {
        File out = new File(root, name);
        String rootCanon = root.getCanonicalPath() + File.separator;
        if (!out.getCanonicalPath().startsWith(rootCanon)) throw new IOException("Geçersiz ZIP yolu: " + name);
        if (!overwrite && out.isFile()) return;
        File parent = out.getParentFile();
        if (parent != null && !parent.isDirectory() && !parent.mkdirs() && !parent.isDirectory()) {
            throw new IOException("Dizin oluşturulamadı: " + parent);
        }
        byte[] buf = new byte[16384];
        try (FileOutputStream fos = new FileOutputStream(out)) {
            int r;
            while ((r = in.read(buf)) > 0) fos.write(buf, 0, r);
        }
    }

    private static void deleteRecursively(File f) {
        if (f.isDirectory()) {
            File[] children = f.listFiles();
            if (children != null) for (File c : children) deleteRecursively(c);
        }
        f.delete();
    }

    private static String readText(File f) throws IOException {
        try (FileInputStream in = new FileInputStream(f)) {
            byte[] buf = new byte[(int) Math.min(262144, f.length())];
            int off = 0, r;
            while (off < buf.length && (r = in.read(buf, off, buf.length - off)) > 0) off += r;
            return new String(buf, 0, off, StandardCharsets.UTF_8);
        }
    }

    private static void writeText(File f, String text) throws IOException {
        try (FileOutputStream out = new FileOutputStream(f)) { out.write(text.getBytes(StandardCharsets.UTF_8)); }
    }
}
