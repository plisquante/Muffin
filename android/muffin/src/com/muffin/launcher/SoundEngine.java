package com.muffin.launcher;

import android.media.AudioAttributes;
import android.media.SoundPool;

import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.nio.charset.StandardCharsets;

/**
 * Loads real downloaded Mojang OGG sound effects from `assets/indexes/<id>.json` +
 * `assets/objects/*` into Android's low-latency SoundPool for block break/place feedback.
 */
public final class SoundEngine {
    private SoundPool pool;
    private int sDigGrass = -1;
    private int sDigStone = -1;
    private int sDigWood = -1;
    private int sPop = -1;

    public void initAsync(final String assetsDir) {
        if (assetsDir == null || assetsDir.isEmpty()) return;
        new Thread(new Runnable() {
            @Override public void run() {
                try {
                    File idxDir = new File(assetsDir, "indexes");
                    File[] files = idxDir.listFiles();
                    if (files == null || files.length == 0) return;
                    File newest = files[0];
                    for (File f : files) if (f.lastModified() > newest.lastModified()) newest = f;
                    byte[] raw;
                    try (FileInputStream in = new FileInputStream(newest)) {
                        raw = new byte[(int) Math.min(4 * 1024 * 1024, newest.length())];
                        int off = 0, r;
                        while (off < raw.length && (r = in.read(raw, off, raw.length - off)) > 0) off += r;
                    }
                    JSONObject objs = new JSONObject(new String(raw, StandardCharsets.UTF_8)).optJSONObject("objects");
                    if (objs == null) return;

                    AudioAttributes attrs = new AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_GAME)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                            .build();
                    pool = new SoundPool.Builder().setMaxStreams(6).setAudioAttributes(attrs).build();
                    sDigGrass = loadObj(objs, assetsDir, "minecraft/sounds/dig/grass1.ogg");
                    sDigStone = loadObj(objs, assetsDir, "minecraft/sounds/dig/stone1.ogg");
                    sDigWood  = loadObj(objs, assetsDir, "minecraft/sounds/dig/wood1.ogg");
                    sPop      = loadObj(objs, assetsDir, "minecraft/sounds/random/pop.ogg");
                } catch (Throwable ignored) {}
            }
        }, "muffin-sound-loader").start();
    }

    private int loadObj(JSONObject objs, String assetsDir, String key) {
        try {
            JSONObject e = objs.optJSONObject(key);
            if (e == null) return -1;
            String hash = e.optString("hash", "");
            if (hash.length() < 2) return -1;
            File f = new File(new File(new File(assetsDir, "objects"), hash.substring(0, 2)), hash);
            return f.isFile() ? pool.load(f.getAbsolutePath(), 1) : -1;
        } catch (Throwable t) {
            return -1;
        }
    }

    public void playBreak(int blockKind) {
        if (pool == null) return;
        int sid = (blockKind == 3 || blockKind == 7 || blockKind == 8) ? sDigStone
                : ((blockKind == 4 || blockKind == 5) ? sDigWood : sDigGrass);
        if (sid > 0) pool.play(sid, 0.75f, 0.75f, 1, 0, 1.0f);
    }

    public void playPlace() {
        if (pool != null && sPop > 0) pool.play(sPop, 0.65f, 0.65f, 1, 0, 1.0f);
    }

    public void release() {
        if (pool != null) {
            try { pool.release(); } catch (Throwable ignored) {}
            pool = null;
        }
    }
}
