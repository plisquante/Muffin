package com.muffin.launcher;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 100% Complete Official Mojang Downloader:
 * 1. Piston-Meta version manifest v2 (latest release / snapshot)
 * 2. Version metadata JSON (<ver>.json)
 * 3. client.jar with streaming progress & SHA-1 verification
 * 4. All Java runtime libraries (libraries/*.jar)
 * 5. Asset index + 100% asset objects (assets/objects/*)
 * 6. Optional native Mojo/Minecraft engine `.so` from URL
 */
public final class MojangDownloader {
    public static final String MANIFEST_URL =
            "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json";
    private static final String RESOURCES_BASE =
            "https://resources.download.minecraft.net/";

    public static final class VersionEntry {
        public final String id;
        public final String type;
        public final String url;

        public VersionEntry(String id, String type, String url) {
            this.id = id;
            this.type = type;
            this.url = url;
        }

        @Override
        public String toString() {
            return id + " (" + type + ")";
        }
    }

    public static final class DownloadResult {
        public final String versionId;
        public final File clientJar;
        public final String assetIndexId;
        public final int librariesCount;
        public final int assetsCount;

        public DownloadResult(String versionId, File clientJar, String assetIndexId, int librariesCount, int assetsCount) {
            this.versionId = versionId;
            this.clientJar = clientJar;
            this.assetIndexId = assetIndexId;
            this.librariesCount = librariesCount;
            this.assetsCount = assetsCount;
        }
    }

    public interface ProgressListener {
        void onProgress(int percent, String stageMessage);
    }

    private static final class DownloadItem {
        final String url;
        final File dst;
        final String sha1;
        final long size;
        DownloadItem(String url, File dst, String sha1, long size) {
            this.url = url; this.dst = dst; this.sha1 = sha1; this.size = size;
        }
    }

    private MojangDownloader() {}

    public static List<VersionEntry> fetchVersions(int maxCount, boolean includeSnapshots) throws Exception {
        String raw = downloadText(MANIFEST_URL);
        JSONObject root = new JSONObject(raw);
        JSONArray versions = root.getJSONArray("versions");
        List<VersionEntry> out = new ArrayList<VersionEntry>();
        for (int i = 0; i < versions.length() && out.size() < maxCount; i++) {
            JSONObject v = versions.getJSONObject(i);
            String type = v.optString("type", "");
            if ("release".equals(type) || (includeSnapshots && "snapshot".equals(type))) {
                out.add(new VersionEntry(v.getString("id"), type, v.getString("url")));
            }
        }
        return out;
    }

    public static VersionEntry resolveTargetVersion(String preferredId, boolean includeSnapshots) throws Exception {
        List<VersionEntry> list = fetchVersions(40, includeSnapshots);
        if (list.isEmpty()) throw new IllegalStateException("Mojang surum listesi bos.");
        if (preferredId != null) {
            for (VersionEntry ve : list) {
                if (ve.id.equals(preferredId)) return ve;
            }
        }
        return list.get(0); // Latest official release
    }

    public static DownloadResult downloadCompleteSetup(
            VersionEntry ver,
            File versionsRoot,
            File librariesRoot,
            File assetsRoot,
            ProgressListener listener) throws Exception {
        File verDir = new File(versionsRoot, ver.id);
        verDir.mkdirs();

        report(listener, 2, "1/5 Sürüm meta verisi indiriliyor: " + ver.id);
        String metaRaw = downloadText(ver.url);
        writeString(new File(verDir, ver.id + ".json"), metaRaw);

        JSONObject meta = new JSONObject(metaRaw);
        JSONObject client = meta.getJSONObject("downloads").getJSONObject("client");
        String clientUrl = client.getString("url");
        String clientSha1 = client.optString("sha1", "");
        long clientSize = client.optLong("size", 0L);

        File clientJar = new File(verDir, "client.jar");
        if (!verifyFile(clientJar, clientSha1, clientSize)) {
            downloadFileStreaming(clientUrl, clientJar, clientSha1, clientSize, listener, 5, 25,
                    "2/5 client.jar (" + Math.max(1, clientSize / (1024 * 1024)) + " MB)");
        } else {
            report(listener, 25, "2/5 client.jar SHA-1 doğrulandı ✓");
        }

        // 3/5: 100% Java Libraries
        int libTotal = 0;
        JSONArray libraries = meta.optJSONArray("libraries");
        if (libraries != null) {
            List<DownloadItem> libQueue = new ArrayList<DownloadItem>();
            for (int i = 0; i < libraries.length(); i++) {
                JSONObject lib = libraries.getJSONObject(i);
                String name = lib.optString("name", "");
                if (name.contains(":natives-") || !rulesAllowLinux(lib.optJSONArray("rules"))) continue;
                JSONObject libDl = lib.optJSONObject("downloads");
                if (libDl == null) continue;
                JSONObject art = libDl.optJSONObject("artifact");
                if (art == null) continue;
                String path = art.optString("path", ""), url = art.optString("url", "");
                if (path.isEmpty() || url.isEmpty()) continue;
                libTotal++;
                File dst = new File(librariesRoot, path);
                String sha1 = art.optString("sha1", "");
                long sz = art.optLong("size", 0L);
                if (!verifyFile(dst, sha1, sz)) libQueue.add(new DownloadItem(url, dst, sha1, sz));
            }
            if (!libQueue.isEmpty()) {
                downloadBatch(libQueue, "3/5 Kütüphane JAR", 25, 48, listener);
            } else {
                report(listener, 48, "3/5 Tüm kütüphaneler (" + libTotal + ") doğrulanmış ✓");
            }
        }

        // 4/5: Asset Index & 100% Asset Objects
        int assetTotal = 0;
        String idxId = "";
        JSONObject assetIdx = meta.optJSONObject("assetIndex");
        if (assetIdx != null) {
            File idxDir = new File(assetsRoot, "indexes");
            idxDir.mkdirs();
            idxId = assetIdx.getString("id");
            String idxSha1 = assetIdx.optString("sha1", "");
            long idxSize = assetIdx.optLong("size", 0L);
            File idxFile = new File(idxDir, idxId + ".json");
            if (!verifyFile(idxFile, idxSha1, idxSize)) {
                report(listener, 50, "4/5 Asset indeksi indiriliyor (" + idxId + ")...");
                downloadFileVerified(assetIdx.getString("url"), idxFile, idxSha1, idxSize);
            }
            JSONObject idxJson = new JSONObject(readString(idxFile));
            JSONObject objects = idxJson.optJSONObject("objects");
            if (objects != null) {
                File objectsDir = new File(assetsRoot, "objects");
                List<DownloadItem> objQueue = new ArrayList<DownloadItem>();
                Iterator<String> keys = objects.keys();
                while (keys.hasNext()) {
                    JSONObject entry = objects.optJSONObject(keys.next());
                    if (entry == null) continue;
                    String hash = entry.optString("hash", "");
                    long sz = entry.optLong("size", 0L);
                    if (hash.length() < 2) continue;
                    assetTotal++;
                    String prefix = hash.substring(0, 2);
                    File dst = new File(new File(objectsDir, prefix), hash);
                    if (!verifyFile(dst, hash, sz)) {
                        objQueue.add(new DownloadItem(RESOURCES_BASE + prefix + "/" + hash, dst, hash, sz));
                    }
                }
                if (!objQueue.isEmpty()) {
                    downloadBatch(objQueue, "4/5 Asset dosyası", 52, 92, listener);
                } else {
                    report(listener, 92, "4/5 Tüm asset dosyaları (" + assetTotal + ") doğrulanmış ✓");
                }
            }
        }
        return new DownloadResult(ver.id, clientJar, idxId, libTotal, assetTotal);
    }

    public static void downloadDirectSo(String urlStr, File dst, ProgressListener listener) throws Exception {
        downloadFileStreaming(urlStr, dst, "", 0L, listener, 92, 97, "Harici Mojo motoru (.so) indiriliyor");
        dst.setReadable(true, false);
        dst.setExecutable(true, false);
    }

    private static void report(ProgressListener l, int pct, String msg) {
        if (l != null) l.onProgress(Math.max(0, Math.min(100, pct)), msg);
    }

    private static boolean rulesAllowLinux(JSONArray rules) {
        if (rules == null || rules.length() == 0) return true;
        boolean allow = false;
        for (int i = 0; i < rules.length(); i++) {
            JSONObject r = rules.optJSONObject(i);
            if (r == null) continue;
            JSONObject os = r.optJSONObject("os");
            boolean matches = (os == null);
            if (os != null) {
                String osName = os.optString("name", "");
                matches = osName.isEmpty() || "linux".equals(osName);
            }
            if (matches) allow = "allow".equals(r.optString("action", "allow"));
        }
        return allow;
    }

    private static void downloadBatch(
            final List<DownloadItem> items,
            final String label,
            final int pctStart,
            final int pctEnd,
            final ProgressListener listener) throws Exception {
        final int total = items.size();
        final AtomicInteger done = new AtomicInteger(0);
        ExecutorService pool = Executors.newFixedThreadPool(6);
        List<Future<Void>> futures = new ArrayList<Future<Void>>(total);
        try {
            for (final DownloadItem item : items) {
                futures.add(pool.submit(new Callable<Void>() {
                    @Override public Void call() throws Exception {
                        downloadFileVerified(item.url, item.dst, item.sha1, item.size);
                        int cur = done.incrementAndGet();
                        if (listener != null && (cur == 1 || cur == total || (cur % 20) == 0)) {
                            int pct = pctStart + (int) (((long) cur * (pctEnd - pctStart)) / Math.max(1, total));
                            report(listener, pct, label + ": " + cur + " / " + total);
                        }
                        return null;
                    }
                }));
            }
            for (Future<Void> f : futures) f.get();
        } finally {
            pool.shutdownNow();
        }
    }

    private static boolean verifyFile(File f, String expectedSha1, long expectedSize) {
        if (!f.isFile()) return false;
        if (expectedSize > 0 && f.length() != expectedSize) return false;
        if (expectedSha1 == null || expectedSha1.isEmpty()) return true;
        try (FileInputStream in = new FileInputStream(f)) {
            MessageDigest md = MessageDigest.getInstance("SHA-1");
            byte[] buf = new byte[32768];
            int r;
            while ((r = in.read(buf)) > 0) md.update(buf, 0, r);
            byte[] d = md.digest();
            StringBuilder hex = new StringBuilder(40);
            for (byte b : d) hex.append(String.format("%02x", b & 0xFF));
            return hex.toString().equalsIgnoreCase(expectedSha1);
        } catch (Exception e) {
            return false;
        }
    }

    private static String downloadText(String urlStr) throws Exception {
        HttpURLConnection conn = openConn(urlStr);
        try (InputStream in = conn.getInputStream(); ByteArrayOutputStream bos = new ByteArrayOutputStream()) {
            byte[] buf = new byte[16384];
            int r;
            while ((r = in.read(buf)) > 0) bos.write(buf, 0, r);
            return new String(bos.toByteArray(), StandardCharsets.UTF_8);
        } finally {
            conn.disconnect();
        }
    }

    private static void downloadFileVerified(String urlStr, File dst, String sha1, long expectedSize) throws Exception {
        downloadFileStreaming(urlStr, dst, sha1, expectedSize, null, 0, 0, "");
    }

    private static void downloadFileStreaming(
            String urlStr, File dst, String sha1, long expectedSize,
            ProgressListener listener, int pctStart, int pctEnd, String label) throws Exception {
        dst.getParentFile().mkdirs();
        File part = new File(dst.getParentFile(), dst.getName() + ".part");
        HttpURLConnection conn = openConn(urlStr);
        try (InputStream in = conn.getInputStream(); FileOutputStream out = new FileOutputStream(part)) {
            long totalLen = expectedSize > 0 ? expectedSize : Math.max(0L, conn.getContentLengthLong());
            byte[] buf = new byte[32768];
            long received = 0, lastReport = 0;
            int r;
            while ((r = in.read(buf)) > 0) {
                out.write(buf, 0, r);
                received += r;
                if (listener != null && totalLen > 0 && (received - lastReport >= 262144 || received == totalLen)) {
                    lastReport = received;
                    int pct = pctStart + (int) ((received * (pctEnd - pctStart)) / totalLen);
                    report(listener, pct, label + ": " + (received / (1024 * 1024)) + " / " + (totalLen / (1024 * 1024)) + " MB");
                }
            }
        } finally {
            conn.disconnect();
        }
        if (!verifyFile(part, sha1, expectedSize)) {
            part.delete();
            throw new IllegalStateException("SHA-1/boyut dogrulama hatasi: " + dst.getName());
        }
        if (dst.exists()) dst.delete();
        if (!part.renameTo(dst)) {
            try (FileInputStream in = new FileInputStream(part); FileOutputStream out = new FileOutputStream(dst)) {
                byte[] buf = new byte[32768];
                int r;
                while ((r = in.read(buf)) > 0) out.write(buf, 0, r);
            }
            part.delete();
        }
    }

    private static HttpURLConnection openConn(String urlStr) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(urlStr).openConnection();
        c.setInstanceFollowRedirects(true);
        c.setConnectTimeout(15000);
        c.setReadTimeout(45000);
        c.setRequestProperty("User-Agent", "MuffinLauncher-MojoEdition/1.0");
        int code = c.getResponseCode();
        if (code < 200 || code >= 300) throw new IllegalStateException("HTTP " + code + " (" + urlStr + ")");
        return c;
    }

    private static void writeString(File f, String s) throws Exception {
        try (FileOutputStream out = new FileOutputStream(f)) { out.write(s.getBytes(StandardCharsets.UTF_8)); }
    }

    private static String readString(File f) throws Exception {
        try (FileInputStream in = new FileInputStream(f); ByteArrayOutputStream bos = new ByteArrayOutputStream()) {
            byte[] buf = new byte[16384];
            int r;
            while ((r = in.read(buf)) > 0) bos.write(buf, 0, r);
            return new String(bos.toByteArray(), StandardCharsets.UTF_8);
        }
    }
}
