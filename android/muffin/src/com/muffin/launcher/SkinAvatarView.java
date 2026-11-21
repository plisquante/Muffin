package com.muffin.launcher;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.View;

public final class SkinAvatarView extends View {
    private String username = "MojoOyuncu";
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF frameRect = new RectF();

    public SkinAvatarView(Context context) {
        super(context);
    }

    public void setUsername(String name) {
        this.username = (name == null || name.isEmpty()) ? "MojoOyuncu" : name;
        invalidate();
    }

    private static int fnvHash(String s) {
        int h = 0x811c9dc5;
        for (int i = 0; i < s.length(); i++) {
            h ^= s.charAt(i);
            h *= 0x01000193;
        }
        return h;
    }

    private static int mixColor(int base, int delta) {
        int r = Math.max(0, Math.min(255, ((base >> 16) & 0xFF) + delta));
        int g = Math.max(0, Math.min(255, ((base >> 8) & 0xFF) + delta));
        int b = Math.max(0, Math.min(255, (base & 0xFF) + delta));
        return 0xFF000000 | (r << 16) | (g << 8) | b;
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        int w = getWidth(), h = getHeight(), size = Math.min(w, h);
        if (size <= 0) return;

        float left = (w - size) * 0.5f, top = (h - size) * 0.5f, cell = size / 8.0f;
        int seed = fnvHash(username);
        int[] skinPalette = {0xFFE2B48E, 0xFFF1C6A8, 0xFFC58C5F, 0xFF8D5524};
        int[] hairPalette = {0xFF1F2937, 0xFF2B1E16, 0xFF0369A1, 0xFFD97706, 0xFF10B981, 0xFF3B82F6};
        int[] eyePalette = {0xFF38BDF8, 0xFF22D3EE, 0xFF10B981, 0xFF818CF8, 0xFFF59E0B};

        int skin = skinPalette[Math.abs(seed) % skinPalette.length];
        int hair = hairPalette[Math.abs(seed >> 4) % hairPalette.length];
        int eye = eyePalette[Math.abs(seed >> 8) % eyePalette.length];
        boolean hasBeard = ((seed >> 12) & 1) == 1;
        boolean hasHeadband = ((seed >> 13) & 3) == 0;

        paint.setStyle(Paint.Style.FILL);
        for (int y = 0; y < 8; y++) {
            for (int x = 0; x < 8; x++) {
                int c = skin;
                int jitter = (((x * 31 + y * 17 + seed) & 7) - 3) * 4;
                if (y <= 1) c = hair;
                else if (y == 2 && (x == 0 || x == 7)) c = hair;
                else if (hasHeadband && y == 2) c = eye;
                else if (y == 4 && (x == 1 || x == 6)) c = 0xFFF8FAFC;
                else if (y == 4 && (x == 2 || x == 5)) c = eye;
                else if (y == 5 && (x == 3 || x == 4)) c = mixColor(skin, -24);
                else if (y >= 6 && hasBeard && (x >= 2 && x <= 5))
                    c = (y == 6 && (x == 3 || x == 4)) ? mixColor(skin, -36) : mixColor(hair, -12);
                else if (y == 7 && (x == 3 || x == 4)) c = mixColor(skin, -32);

                paint.setColor(mixColor(c, jitter));
                canvas.drawRect(left + x * cell, top + y * cell,
                        left + (x + 1) * cell + 0.5f, top + (y + 1) * cell + 0.5f, paint);
            }
        }
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(Math.max(2f, size * 0.045f));
        paint.setColor(0xFF38BDF8);
        frameRect.set(left + 1f, top + 1f, left + size - 1f, top + size - 1f);
        canvas.drawRoundRect(frameRect, size * 0.1f, size * 0.1f, paint);
    }
}
