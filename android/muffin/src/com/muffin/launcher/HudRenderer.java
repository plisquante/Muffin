package com.muffin.launcher;

import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;

public final class HudRenderer {
    private static final Path TMP_PATH = new Path();
    private static final RectF TMP_RECT = new RectF();

    private static final String[] HOTBAR_NAMES = {
        "Çim Bloğu", "Toprak", "Taş", "Meşe Tahtası", "Meşe Kütüğü",
        "Yaprak", "Kırıktaş", "Mojo Kristali", "Işıktaşı"
    };
    private static final int[] HOTBAR_TOP_COLORS = {
        0xFF59B337, 0xFF8B5A2B, 0xFF94A3B8, 0xFFC89B54, 0xFF7D5333,
        0xFF2D8A4E, 0xFF788290, 0xFF38BDF8, 0xFFFDE047
    };
    private static final int[] HOTBAR_SIDE_COLORS = {
        0xFF785134, 0xFF6B4220, 0xFF6E7A8A, 0xFFA47A3C, 0xFF563720,
        0xFF1F6337, 0xFF555E6B, 0xFF0284C7, 0xFFEAB308
    };

    private HudRenderer() {}

    public static float dp(float v, float density) {
        return v * density;
    }

    public static void computeButtonRect(ControlButtonSpec b, int viewW, int viewH, float density, RectF out) {
        float w = b.widthDp * density, h = b.heightDp * density;
        float cx = b.xPct * viewW, cy = b.yPct * viewH;
        out.set(cx - w * 0.5f, cy - h * 0.5f, cx + w * 0.5f, cy + h * 0.5f);
    }

    public static int guiScale(int viewW, int viewH) {
        return Math.max(1, Math.min(viewW / 320, viewH / 240));
    }

    public static RectF getHotbarRect(int viewW, int viewH, float density) {
        float s = guiScale(viewW, viewH);
        float totalW = 182f * s, barH = 22f * s;
        float left = (viewW - totalW) * 0.5f;
        return new RectF(left, viewH - barH, left + totalW, viewH);
    }

    public static int hotbarSlotAt(int viewW, int viewH, float density, float x) {
        RectF bar = getHotbarRect(viewW, viewH, density);
        float s = guiScale(viewW, viewH);
        float rel = (x - bar.left - s) / (20f * s);
        return (rel < 0f || rel >= 9f) ? -1 : (int) rel;
    }

    public static void drawButton(
            Canvas canvas, RectF rect, ControlButtonSpec b, boolean active,
            boolean selectedInEditor, boolean showEditorBadges, float density, Paint paint) {
        float corner = (b.shape == ControlButtonSpec.SHAPE_CIRCLE || b.shape == ControlButtonSpec.SHAPE_PILL)
                ? Math.min(rect.width(), rect.height()) * 0.5f : dp(10f, density);
        int tint = b.accentColor, alphaNorm = Math.round(b.opacity * 255f);

        paint.setStyle(Paint.Style.FILL);
        paint.setColor(Color.argb(Math.round(alphaNorm * 0.30f), 0, 0, 0));
        TMP_RECT.set(rect.left, rect.top + dp(1.5f, density), rect.right, rect.bottom + dp(1.5f, density));
        canvas.drawRoundRect(TMP_RECT, corner, corner, paint);

        paint.setColor(active
                ? Color.argb(Math.min(240, Math.round(alphaNorm * 0.65f)), Color.red(tint), Color.green(tint), Color.blue(tint))
                : Color.argb(Math.round(alphaNorm * 0.52f), 11, 17, 26));
        canvas.drawRoundRect(rect, corner, corner, paint);

        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(dp(selectedInEditor ? 2.4f : (active ? 1.8f : 1.25f), density));
        paint.setColor(selectedInEditor ? 0xFFFACC15
                : (active ? Color.argb(Math.min(255, alphaNorm + 40), Color.red(tint), Color.green(tint), Color.blue(tint))
                          : Color.argb(Math.round(alphaNorm * 0.48f), 255, 255, 255)));
        canvas.drawRoundRect(rect, corner, corner, paint);

        int fgAlpha = Math.min(255, Math.round(b.opacity * 255f + 25f));
        int iconColor = active ? 0xFFFFFFFF : Color.argb(fgAlpha,
                (Color.red(tint) + 510) / 3, (Color.green(tint) + 510) / 3, (Color.blue(tint) + 510) / 3);
        float minSide = Math.min(rect.width(), rect.height());

        if (b.iconKind != ControlButtonSpec.ICON_TEXT) {
            boolean showSubLabel = minSide >= dp(46f, density) && b.label != null && !b.label.isEmpty();
            float iconCy = showSubLabel ? (rect.centerY() - minSide * 0.10f) : rect.centerY();
            float iconR = minSide * (showSubLabel ? 0.23f : 0.27f);
            drawVectorIcon(canvas, b.iconKind, rect.centerX(), iconCy, iconR, iconColor, density, paint);
            if (showSubLabel) {
                paint.setStyle(Paint.Style.FILL);
                paint.setTextAlign(Paint.Align.CENTER);
                paint.setTypeface(Typeface.DEFAULT_BOLD);
                paint.setTextSize(dp(8.5f, density));
                paint.setColor(Color.argb(Math.round(fgAlpha * 0.88f), 241, 245, 249));
                canvas.drawText(b.label, rect.centerX(), rect.bottom - dp(5.5f, density), paint);
            }
        } else {
            paint.setStyle(Paint.Style.FILL);
            paint.setTextAlign(Paint.Align.CENTER);
            paint.setTypeface(Typeface.DEFAULT_BOLD);
            String txt = b.label != null ? b.label : "";
            float tSize = Math.min(minSide * 0.36f, dp(12.5f, density));
            if (txt.length() > 5) tSize *= 0.78f;
            paint.setTextSize(Math.max(dp(8.5f, density), tSize));
            paint.setColor(iconColor);
            canvas.drawText(txt, rect.centerX(), rect.centerY() - ((paint.descent() + paint.ascent()) * 0.5f), paint);
        }

        if (showEditorBadges) {
            String tag = (b.toggleMode ? "T" : "") + (b.swipeable ? "S" : "") + (b.locked ? "L" : "");
            if (!tag.isEmpty()) {
                paint.setStyle(Paint.Style.FILL);
                paint.setTextAlign(Paint.Align.RIGHT);
                paint.setTypeface(Typeface.MONOSPACE);
                paint.setTextSize(dp(7.5f, density));
                paint.setColor(0xFFFACC15);
                canvas.drawText(tag, rect.right - dp(4f, density), rect.top + dp(9.5f, density), paint);
            }
        }
    }

    private static void drawVectorIcon(
            Canvas canvas, int kind, float cx, float cy, float r, int color, float density, Paint p) {
        p.setColor(color);
        p.setStrokeCap(Paint.Cap.ROUND);
        p.setStrokeJoin(Paint.Join.ROUND);
        switch (kind) {
            case ControlButtonSpec.ICON_SWORD:
                p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(dp(2.1f, density));
                canvas.drawLine(cx - r * 0.7f, cy + r * 0.7f, cx + r * 0.78f, cy - r * 0.78f, p);
                canvas.drawLine(cx - r * 0.55f, cy + r * 0.15f, cx - r * 0.15f, cy + r * 0.55f, p);
                break;
            case ControlButtonSpec.ICON_CUBE: {
                p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(dp(1.7f, density));
                float h = r * 0.88f, w = r * 0.80f;
                TMP_PATH.reset();
                TMP_PATH.moveTo(cx, cy - h); TMP_PATH.lineTo(cx + w, cy - h * 0.45f);
                TMP_PATH.lineTo(cx + w, cy + h * 0.45f); TMP_PATH.lineTo(cx, cy + h);
                TMP_PATH.lineTo(cx - w, cy + h * 0.45f); TMP_PATH.lineTo(cx - w, cy - h * 0.45f);
                TMP_PATH.close(); canvas.drawPath(TMP_PATH, p);
                canvas.drawLine(cx - w, cy - h * 0.45f, cx, cy + dp(0.5f, density), p);
                canvas.drawLine(cx + w, cy - h * 0.45f, cx, cy + dp(0.5f, density), p);
                canvas.drawLine(cx, cy + dp(0.5f, density), cx, cy + h, p);
                break;
            }
            case ControlButtonSpec.ICON_JUMP:
                p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(dp(2.3f, density));
                TMP_PATH.reset();
                TMP_PATH.moveTo(cx - r * 0.68f, cy + r * 0.15f);
                TMP_PATH.lineTo(cx, cy - r * 0.62f);
                TMP_PATH.lineTo(cx + r * 0.68f, cy + r * 0.15f);
                canvas.drawPath(TMP_PATH, p);
                canvas.drawLine(cx, cy - r * 0.52f, cx, cy + r * 0.72f, p);
                break;
            case ControlButtonSpec.ICON_SNEAK:
                p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(dp(2.1f, density));
                TMP_PATH.reset();
                TMP_PATH.moveTo(cx - r * 0.62f, cy - r * 0.25f);
                TMP_PATH.lineTo(cx, cy + r * 0.35f);
                TMP_PATH.lineTo(cx + r * 0.62f, cy - r * 0.25f);
                canvas.drawPath(TMP_PATH, p);
                canvas.drawLine(cx - r * 0.62f, cy + r * 0.66f, cx + r * 0.62f, cy + r * 0.66f, p);
                break;
            case ControlButtonSpec.ICON_SPRINT:
                p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(dp(2.0f, density));
                for (int i = -1; i <= 0; i++) {
                    float ox = i * r * 0.46f;
                    TMP_PATH.reset();
                    TMP_PATH.moveTo(cx + ox - r * 0.16f, cy - r * 0.55f);
                    TMP_PATH.lineTo(cx + ox + r * 0.42f, cy);
                    TMP_PATH.lineTo(cx + ox - r * 0.16f, cy + r * 0.55f);
                    canvas.drawPath(TMP_PATH, p);
                }
                break;
            case ControlButtonSpec.ICON_INV: {
                p.setStyle(Paint.Style.FILL);
                float dotR = Math.max(dp(1.4f, density), r * 0.17f), step = r * 0.55f;
                for (int gy = -1; gy <= 1; gy++)
                    for (int gx = -1; gx <= 1; gx++)
                        canvas.drawCircle(cx + gx * step, cy + gy * step, dotR, p);
                break;
            }
            case ControlButtonSpec.ICON_DROP:
                p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(dp(1.9f, density));
                canvas.drawLine(cx, cy - r * 0.68f, cx, cy + r * 0.36f, p);
                TMP_PATH.reset();
                TMP_PATH.moveTo(cx - r * 0.42f, cy); TMP_PATH.lineTo(cx, cy + r * 0.42f); TMP_PATH.lineTo(cx + r * 0.42f, cy);
                canvas.drawPath(TMP_PATH, p);
                canvas.drawLine(cx - r * 0.65f, cy + r * 0.72f, cx + r * 0.65f, cy + r * 0.72f, p);
                break;
            case ControlButtonSpec.ICON_CHAT:
                p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(dp(1.7f, density));
                TMP_RECT.set(cx - r * 0.78f, cy - r * 0.56f, cx + r * 0.78f, cy + r * 0.42f);
                canvas.drawRoundRect(TMP_RECT, dp(3f, density), dp(3f, density), p);
                canvas.drawLine(cx - r * 0.35f, cy + r * 0.42f, cx - r * 0.48f, cy + r * 0.78f, p);
                break;
            case ControlButtonSpec.ICON_PAUSE: {
                p.setStyle(Paint.Style.FILL);
                float bw = Math.max(dp(2.2f, density), r * 0.24f), bh = r * 0.64f, gap = r * 0.32f;
                canvas.drawRoundRect(new RectF(cx - gap - bw, cy - bh, cx - gap, cy + bh), dp(1.2f, density), dp(1.2f, density), p);
                canvas.drawRoundRect(new RectF(cx + gap, cy - bh, cx + gap + bw, cy + bh), dp(1.2f, density), dp(1.2f, density), p);
                break;
            }
            case ControlButtonSpec.ICON_CURSOR:
                p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(dp(1.8f, density));
                TMP_PATH.reset();
                TMP_PATH.moveTo(cx - r * 0.42f, cy - r * 0.72f);
                TMP_PATH.lineTo(cx - r * 0.42f, cy + r * 0.65f);
                TMP_PATH.lineTo(cx - r * 0.05f, cy + r * 0.32f);
                TMP_PATH.lineTo(cx + r * 0.34f, cy + r * 0.74f);
                TMP_PATH.lineTo(cx + r * 0.52f, cy + r * 0.14f);
                TMP_PATH.close(); canvas.drawPath(TMP_PATH, p);
                break;
            case ControlButtonSpec.ICON_KEYBOARD:
                p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(dp(1.6f, density));
                TMP_RECT.set(cx - r * 0.85f, cy - r * 0.52f, cx + r * 0.85f, cy + r * 0.52f);
                canvas.drawRoundRect(TMP_RECT, dp(2.5f, density), dp(2.5f, density), p);
                canvas.drawLine(cx - r * 0.42f, cy + r * 0.24f, cx + r * 0.42f, cy + r * 0.24f, p);
                break;
            case ControlButtonSpec.ICON_CAMERA:
                p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(dp(1.7f, density));
                TMP_RECT.set(cx - r * 0.82f, cy - r * 0.46f, cx + r * 0.82f, cy + r * 0.56f);
                canvas.drawRoundRect(TMP_RECT, dp(2.5f, density), dp(2.5f, density), p);
                canvas.drawCircle(cx, cy + r * 0.05f, r * 0.28f, p);
                break;
            case ControlButtonSpec.ICON_SWAP:
                p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(dp(1.8f, density));
                canvas.drawLine(cx - r * 0.65f, cy - r * 0.26f, cx + r * 0.65f, cy - r * 0.26f, p);
                canvas.drawLine(cx + r * 0.32f, cy - r * 0.55f, cx + r * 0.65f, cy - r * 0.26f, p);
                canvas.drawLine(cx - r * 0.65f, cy + r * 0.26f, cx + r * 0.65f, cy + r * 0.26f, p);
                canvas.drawLine(cx - r * 0.32f, cy + r * 0.55f, cx - r * 0.65f, cy + r * 0.26f, p);
                break;
            case ControlButtonSpec.ICON_PICK:
                p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(dp(1.8f, density));
                canvas.drawCircle(cx, cy, r * 0.68f, p);
                canvas.drawLine(cx - r * 0.32f, cy, cx + r * 0.32f, cy, p);
                canvas.drawLine(cx, cy - r * 0.32f, cx, cy + r * 0.32f, p);
                break;
            case ControlButtonSpec.ICON_EYE:
                p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(dp(1.7f, density));
                TMP_RECT.set(cx - r * 0.82f, cy - r * 0.44f, cx + r * 0.82f, cy + r * 0.44f);
                canvas.drawOval(TMP_RECT, p);
                p.setStyle(Paint.Style.FILL);
                canvas.drawCircle(cx, cy, r * 0.24f, p);
                break;
            case ControlButtonSpec.ICON_MENU:
                p.setStyle(Paint.Style.STROKE); p.setStrokeWidth(dp(1.9f, density));
                canvas.drawLine(cx - r * 0.6f, cy - r * 0.45f, cx + r * 0.6f, cy - r * 0.45f, p);
                canvas.drawLine(cx - r * 0.6f, cy, cx + r * 0.6f, cy, p);
                canvas.drawLine(cx - r * 0.6f, cy + r * 0.45f, cx + r * 0.6f, cy + r * 0.45f, p);
                break;
        }
    }

    public static void drawMovementPad(
            Canvas canvas, ControlProfile profile, float cx, float cy,
            float thumbX, float thumbY, float moveX, float moveY,
            boolean activeTouch, boolean selectedInEditor, float density, Paint paint) {
        float halfSpan = (profile.joystickSizeDp * 0.5f) * density;
        int alpha = Math.round(profile.joystickOpacity * 255f);

        if (profile.joystickMode == ControlProfile.STICK_DPAD) {
            float cell = halfSpan * 0.62f, gap = dp(2.5f, density);
            boolean wOn = moveY > 0.25f, sOn = moveY < -0.25f, aOn = moveX < -0.25f, dOn = moveX > 0.25f;
            drawDpadArm(canvas, cx, cy - cell - gap, cell, wOn, selectedInEditor, alpha, 0, density, paint);
            drawDpadArm(canvas, cx, cy + cell + gap, cell, sOn, selectedInEditor, alpha, 1, density, paint);
            drawDpadArm(canvas, cx - cell - gap, cy, cell, aOn, selectedInEditor, alpha, 2, density, paint);
            drawDpadArm(canvas, cx + cell + gap, cy, cell, dOn, selectedInEditor, alpha, 3, density, paint);
            TMP_RECT.set(cx - cell * 0.46f, cy - cell * 0.46f, cx + cell * 0.46f, cy + cell * 0.46f);
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(Color.argb(Math.round(alpha * 0.46f), 14, 20, 28));
            canvas.drawRoundRect(TMP_RECT, dp(8f, density), dp(8f, density), paint);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(dp(selectedInEditor ? 2f : 1.1f, density));
            paint.setColor(selectedInEditor ? 0xFFFACC15 : Color.argb(Math.round(alpha * 0.40f), 255, 255, 255));
            canvas.drawRoundRect(TMP_RECT, dp(8f, density), dp(8f, density), paint);
            return;
        }

        paint.setStyle(Paint.Style.FILL);
        paint.setColor(Color.argb(Math.round(alpha * 0.34f), 13, 18, 26));
        canvas.drawCircle(cx, cy, halfSpan, paint);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(dp(selectedInEditor ? 2.4f : 1.5f, density));
        paint.setColor(selectedInEditor ? 0xFFFACC15 : Color.argb(Math.round(alpha * 0.45f), 255, 255, 255));
        canvas.drawCircle(cx, cy, halfSpan, paint);

        float tx = activeTouch ? thumbX : cx, ty = activeTouch ? thumbY : cy, knobR = halfSpan * 0.40f;
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(activeTouch
                ? Color.argb(Math.min(240, Math.round(alpha * 0.80f)), 56, 189, 248)
                : Color.argb(Math.round(alpha * 0.58f), 226, 232, 240));
        canvas.drawCircle(tx, ty, knobR, paint);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(dp(1.5f, density));
        paint.setColor(Color.argb(Math.min(255, alpha + 30), 255, 255, 255));
        canvas.drawCircle(tx, ty, knobR, paint);
    }

    private static void drawDpadArm(
            Canvas canvas, float cx, float cy, float size, boolean active, boolean selected,
            int alpha, int dir, float density, Paint paint) {
        float half = size * 0.5f;
        TMP_RECT.set(cx - half, cy - half, cx + half, cy + half);
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(active
                ? Color.argb(Math.min(230, Math.round(alpha * 0.72f)), 56, 189, 248)
                : Color.argb(Math.round(alpha * 0.50f), 14, 20, 28));
        canvas.drawRoundRect(TMP_RECT, dp(8f, density), dp(8f, density), paint);

        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(dp(selected ? 2.2f : 1.25f, density));
        paint.setColor(selected ? 0xFFFACC15 : active ? 0xFF38BDF8 : Color.argb(Math.round(alpha * 0.46f), 255, 255, 255));
        canvas.drawRoundRect(TMP_RECT, dp(8f, density), dp(8f, density), paint);

        float ar = size * 0.22f;
        paint.setStrokeWidth(dp(2.0f, density));
        paint.setColor(Color.argb(Math.min(255, alpha + 35), 255, 255, 255));
        TMP_PATH.reset();
        if (dir == 0) { TMP_PATH.moveTo(cx - ar, cy + ar * 0.45f); TMP_PATH.lineTo(cx, cy - ar * 0.55f); TMP_PATH.lineTo(cx + ar, cy + ar * 0.45f); }
        else if (dir == 1) { TMP_PATH.moveTo(cx - ar, cy - ar * 0.45f); TMP_PATH.lineTo(cx, cy + ar * 0.55f); TMP_PATH.lineTo(cx + ar, cy - ar * 0.45f); }
        else if (dir == 2) { TMP_PATH.moveTo(cx + ar * 0.45f, cy - ar); TMP_PATH.lineTo(cx - ar * 0.55f, cy); TMP_PATH.lineTo(cx + ar * 0.45f, cy + ar); }
        else { TMP_PATH.moveTo(cx - ar * 0.45f, cy - ar); TMP_PATH.lineTo(cx + ar * 0.55f, cy); TMP_PATH.lineTo(cx - ar * 0.45f, cy + ar); }
        canvas.drawPath(TMP_PATH, paint);
    }

    public static void drawCrosshair(Canvas canvas, int w, int h, float density, Paint paint) {
        float cx = w * 0.5f, cy = h * 0.5f, arm = dp(7.5f, density), gap = dp(1.5f, density);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeCap(Paint.Cap.SQUARE);
        paint.setStrokeWidth(dp(3.0f, density));
        paint.setColor(0x99000000);
        canvas.drawLine(cx - arm, cy, cx - gap, cy, paint);
        canvas.drawLine(cx + gap, cy, cx + arm, cy, paint);
        canvas.drawLine(cx, cy - arm, cx, cy - gap, paint);
        canvas.drawLine(cx, cy + gap, cx, cy + arm, paint);

        paint.setStrokeWidth(dp(1.6f, density));
        paint.setColor(0xEEFFFFFF);
        canvas.drawLine(cx - arm, cy, cx - gap, cy, paint);
        canvas.drawLine(cx + gap, cy, cx + arm, cy, paint);
        canvas.drawLine(cx, cy - arm, cx, cy - gap, paint);
        canvas.drawLine(cx, cy + gap, cx, cy + arm, paint);
    }

    public static void drawHotbar(Canvas canvas, int w, int h, int selectedSlot, boolean showName, float density, Paint paint) {
        RectF bar = getHotbarRect(w, h, density);
        float slotW = bar.width() / 9f;
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(0xA60E141E);
        canvas.drawRoundRect(bar, dp(7f, density), dp(7f, density), paint);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(dp(1.2f, density));
        paint.setColor(0x66FFFFFF);
        canvas.drawRoundRect(bar, dp(7f, density), dp(7f, density), paint);
        for (int i = 0; i < 9; i++) {
            float sx = bar.left + i * slotW;
            TMP_RECT.set(sx + dp(2.5f, density), bar.top + dp(2.5f, density),
                    sx + slotW - dp(2.5f, density), bar.bottom - dp(2.5f, density));
            if (i == selectedSlot) {
                paint.setStyle(Paint.Style.FILL); paint.setColor(0x3338BDF8);
                canvas.drawRoundRect(TMP_RECT, dp(4f, density), dp(4f, density), paint);
                paint.setStyle(Paint.Style.STROKE); paint.setStrokeWidth(dp(1.8f, density)); paint.setColor(0xFF38BDF8);
                canvas.drawRoundRect(TMP_RECT, dp(4f, density), dp(4f, density), paint);
            }
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(HOTBAR_TOP_COLORS[i]);
            float bsz = dp(11f, density);
            canvas.drawRect(TMP_RECT.centerX() - bsz * 0.5f, TMP_RECT.centerY() - bsz * 0.5f,
                    TMP_RECT.centerX() + bsz * 0.5f, TMP_RECT.centerY() + bsz * 0.5f, paint);
        }
        if (showName && selectedSlot >= 0 && selectedSlot < HOTBAR_NAMES.length) {
            paint.setStyle(Paint.Style.FILL); paint.setTextAlign(Paint.Align.CENTER);
            paint.setTypeface(Typeface.DEFAULT_BOLD); paint.setTextSize(dp(11f, density));
            paint.setColor(0xFFF8FAFC);
            canvas.drawText(HOTBAR_NAMES[selectedSlot], bar.centerX(), bar.top - dp(8f, density), paint);
        }
    }

    public static void drawEditorWorldBackdrop(Canvas canvas, int w, int h, float density, Paint paint) {
        paint.setStyle(Paint.Style.FILL);
        paint.setShader(new LinearGradient(0, 0, 0, h * 0.62f, 0xFF0284C7, 0xFFBAE6FD, Shader.TileMode.CLAMP));
        canvas.drawRect(0, 0, w, h * 0.62f, paint);
        paint.setShader(null);
        paint.setColor(0xFF3B873E); canvas.drawRect(0, h * 0.56f, w, h * 0.72f, paint);
        paint.setColor(0xFF6B4226); canvas.drawRect(0, h * 0.72f, w, h, paint);
        drawCrosshair(canvas, w, h, density, paint);
        drawHotbar(canvas, w, h, 0, false, density, paint);
    }
}
