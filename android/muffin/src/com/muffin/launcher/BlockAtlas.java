package com.muffin.launcher;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;

import java.io.File;

/**
 * Loads real 16x16 block textures extracted from downloaded Mojang `client.jar`
 * (`resources/assets/minecraft/textures/block/*.png`) into a crisp 256x256 RGBA Atlas.
 * Falls back cleanly to crisp pixel-art procedural tiles if not downloaded yet.
 */
public final class BlockAtlas {
    public static final int ATLAS_TILES = 16;
    public static final int TILE_PX = 16;
    public static final int ATLAS_PX = ATLAS_TILES * TILE_PX; // 256

    // Tile slots matching C shader / chunk mesher:
    //  0: grass_top   1: grass_side   2: dirt       3: stone
    //  4: oak_planks  5: oak_log      6: oak_log_top 7: oak_leaves
    //  8: cobblestone 9: sand        10: diamond    11: glowstone
    // 12: bricks     13: glass       14: bedrock    15: water
    private static final String[] TEX_FILES = {
        "grass_block_top.png",
        "grass_block_side.png",
        "dirt.png",
        "stone.png",
        "oak_planks.png",
        "oak_log.png",
        "oak_log_top.png",
        "oak_leaves.png",
        "cobblestone.png",
        "sand.png",
        "diamond_block.png",
        "glowstone.png",
        "bricks.png",
        "glass.png",
        "bedrock.png",
        "water_still.png"
    };

    private static final int[] BASE_COLORS = {
        0xFF5FB847, 0xFF7D5333, 0xFF7E5535, 0xFF888D94,
        0xFFC29555, 0xFF6B4628, 0xFFB8925A, 0xFF39913F,
        0xFF727880, 0xFFE3D396, 0xFF38BDF8, 0xFFFDE047,
        0xFFA25243, 0x99CBE8FA, 0xFF3A3C42, 0xCC2C84E8
    };

    private BlockAtlas() {}

    public static byte[] buildAtlasRgba(String resourcesDir) {
        byte[] rgba = new byte[ATLAS_PX * ATLAS_PX * 4];
        File blockDir = (resourcesDir != null && !resourcesDir.isEmpty())
                ? new File(resourcesDir, "assets/minecraft/textures/block")
                : null;
        File grassOverlay = blockDir != null ? new File(blockDir, "grass_block_side_overlay.png") : null;
        Bitmap overlayBmp = (grassOverlay != null && grassOverlay.isFile())
                ? BitmapFactory.decodeFile(grassOverlay.getAbsolutePath()) : null;

        for (int t = 0; t < TEX_FILES.length; t++) {
            File png = (blockDir != null) ? new File(blockDir, TEX_FILES[t]) : null;
            Bitmap bmp = (png != null && png.isFile()) ? BitmapFactory.decodeFile(png.getAbsolutePath()) : null;
            int tx = (t % ATLAS_TILES) * TILE_PX;
            int ty = (t / ATLAS_TILES) * TILE_PX;

            for (int py = 0; py < TILE_PX; py++) {
                for (int px = 0; px < TILE_PX; px++) {
                    int argb;
                    if (bmp != null) {
                        int sx = (px * bmp.getWidth()) / TILE_PX;
                        int sy = (py * Math.min(bmp.getWidth(), bmp.getHeight())) / TILE_PX;
                        argb = bmp.getPixel(sx, sy);
                        if (t == 0) {
                            argb = tintMul(argb, 128, 206, 89); // Biome grass tint
                        } else if (t == 7) {
                            argb = tintMul(argb, 92, 185, 76);  // Biome oak leaf tint
                            if (((argb >>> 24) & 0xFF) < 64) argb = 0xFF2B6F2E;
                        } else if (t == 15) {
                            argb = tintMul(argb, 56, 148, 248); // Water tint
                            argb = (0xC8 << 24) | (argb & 0x00FFFFFF);
                        } else if (t == 1 && overlayBmp != null) {
                            int osx = (px * overlayBmp.getWidth()) / TILE_PX;
                            int osy = (py * Math.min(overlayBmp.getWidth(), overlayBmp.getHeight())) / TILE_PX;
                            int ov = overlayBmp.getPixel(osx, osy);
                            if (((ov >>> 24) & 0xFF) > 128) {
                                argb = tintMul(ov, 128, 206, 89);
                            }
                        }
                    } else {
                        argb = proceduralPixel(t, px, py);
                    }
                    int off = ((ty + py) * ATLAS_PX + (tx + px)) * 4;
                    rgba[off]     = (byte) ((argb >> 16) & 0xFF);
                    rgba[off + 1] = (byte) ((argb >> 8) & 0xFF);
                    rgba[off + 2] = (byte) (argb & 0xFF);
                    rgba[off + 3] = (byte) ((argb >> 24) & 0xFF);
                }
            }
            if (bmp != null) bmp.recycle();
        }
        if (overlayBmp != null) overlayBmp.recycle();
        return rgba;
    }

    private static int tintMul(int argb, int tr, int tg, int tb) {
        int a = (argb >>> 24) & 0xFF;
        int r = (((argb >> 16) & 0xFF) * tr) / 255;
        int g = (((argb >> 8) & 0xFF) * tg) / 255;
        int b = ((argb & 0xFF) * tb) / 255;
        return (a << 24) | (r << 16) | (g << 8) | b;
    }

    private static int proceduralPixel(int t, int x, int y) {
        int base = BASE_COLORS[t];
        int h = ((x * 73 + y * 139 + t * 37) & 15) - 8;
        if (t == 1 && y < 4 + ((x * 5) & 1)) base = 0xFF5FB847; // grass top rim on side
        if (t == 13) {
            if (x == 0 || x == 15 || y == 0 || y == 15) return 0xFFFFFFFF;
            if ((x + y == 6) || (x + y == 7)) return 0xAAFFFFFF;
            return 0x22CBE8FA;
        }
        if (t == 4 && (y % 4 == 0)) h -= 22; // planks groove
        if (t == 12 && ((y % 4 == 0) || ((x + (y / 4) * 4) % 8 == 0))) return 0xFFCBD5E1; // brick mortar
        if (t == 10 && (x == 0 || x == 15 || y == 0 || y == 15)) h += 24; // crystal edge highlight
        int a = (base >>> 24) & 0xFF;
        int r = Math.max(0, Math.min(255, ((base >> 16) & 0xFF) + h));
        int g = Math.max(0, Math.min(255, ((base >> 8) & 0xFF) + h));
        int b = Math.max(0, Math.min(255, (base & 0xFF) + h));
        return (a << 24) | (r << 16) | (g << 8) | b;
    }
}
