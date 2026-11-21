package com.muffin.launcher;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.view.HapticFeedbackConstants;
import android.view.MotionEvent;
import android.view.View;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

public final class TouchOverlayView extends View {
    public interface HostCallbacks {
        void onOpenQuickMenu();
        void onToggleSoftKeyboard();
    }

    private final LauncherConfig config;
    private ControlProfile profile;
    private HostCallbacks callbacks;

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF tmpRect = new RectF();
    private final Path cursorPath = new Path();

    private final Map<Integer, String> pointerButtonMap = new HashMap<Integer, String>();
    private final Map<Integer, float[]> pointerLastXY = new HashMap<Integer, float[]>();
    private final Set<String> heldButtons = new HashSet<String>();
    private final Set<String> toggledButtons = new HashSet<String>();

    private int stickPointerId = -1;
    private float stickOriginX, stickOriginY, stickThumbX, stickThumbY, currentMoveX, currentMoveY;
    private int lookPointerId = -1;
    private float lastLookX, lastLookY;

    private boolean cursorMode = false;
    private float cursorNormX = 0.5f, cursorNormY = 0.5f;
    private int cursorPointerId = -1;
    private float cursorDownX, cursorDownY;
    private boolean cursorDragged;

    private boolean hudHidden = false;
    private boolean showF3Hud;
    private final long[] telemetry = new long[16];
    private String username = "MojoOyuncu";
    private String engineModeLabel = "Mojo 1.2 Native Engine";
    private int selectedHotbar = 0;

    public TouchOverlayView(Context context, LauncherConfig config, ControlProfile profile) {
        super(context);
        this.config = config;
        this.profile = profile;
        this.showF3Hud = config.autoF3;
        this.username = config.username;
        setFocusable(false);
    }

    public void setHostCallbacks(HostCallbacks cb) { this.callbacks = cb; }

    public void setProfile(ControlProfile newProfile) {
        releaseAllInputs();
        this.profile = newProfile;
        invalidate();
    }

    public boolean isShowF3Hud() { return showF3Hud; }

    public void toggleF3Hud() { this.showF3Hud = !this.showF3Hud; invalidate(); }

    public void setEngineModeLabel(String label) { this.engineModeLabel = label; }

    public void updateTelemetry(long[] out16) {
        if (out16 != null && out16.length >= 16) {
            System.arraycopy(out16, 0, telemetry, 0, 16);
            invalidate();
        }
    }

    public void releaseAllInputs() {
        for (String id : heldButtons) {
            ControlButtonSpec b = findButton(id);
            if (b != null) GlfwKeys.dispatch(b.actionKind, b.keyCode, false);
        }
        heldButtons.clear();
        for (String id : toggledButtons) {
            ControlButtonSpec b = findButton(id);
            if (b != null) GlfwKeys.dispatch(b.actionKind, b.keyCode, false);
        }
        toggledButtons.clear();
        pointerButtonMap.clear();
        pointerLastXY.clear();
        stickPointerId = -1; lookPointerId = -1; cursorPointerId = -1;
        currentMoveX = 0f; currentMoveY = 0f;
        if (NativeBridge.isLoaded()) NativeBridge.nativeSetMoveAxis(0f, 0f);
        invalidate();
    }

    private ControlButtonSpec findButton(String id) {
        if (profile == null) return null;
        for (ControlButtonSpec b : profile.buttons) if (b.id.equals(id)) return b;
        return null;
    }

    private float dp(float v) { return v * getResources().getDisplayMetrics().density; }

    private RectF getQuickMenuRect(int w, float density) {
        float mw = HudRenderer.dp(88f, density), mh = HudRenderer.dp(22f, density);
        return new RectF(w * 0.5f - mw * 0.5f, HudRenderer.dp(5f, density),
                w * 0.5f + mw * 0.5f, HudRenderer.dp(5f, density) + mh);
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        int w = getWidth(), h = getHeight();
        if (w <= 0 || h <= 0 || profile == null) return;
        float density = getResources().getDisplayMetrics().density;

        if (!hudHidden) {
            HudRenderer.drawCrosshair(canvas, w, h, density, paint);
            HudRenderer.drawHotbar(canvas, w, h, selectedHotbar, true, density, paint);
            float jx = (stickPointerId != -1 && profile.dynamicJoystick) ? stickOriginX : (profile.joystickXPct * w);
            float jy = (stickPointerId != -1 && profile.dynamicJoystick) ? stickOriginY : (profile.joystickYPct * h);
            HudRenderer.drawMovementPad(canvas, profile, jx, jy, stickThumbX, stickThumbY,
                    currentMoveX, currentMoveY, stickPointerId != -1, false, density, paint);
            for (ControlButtonSpec b : profile.buttons) {
                if (!b.visible) continue;
                HudRenderer.computeButtonRect(b, w, h, density, tmpRect);
                boolean active = heldButtons.contains(b.id) || toggledButtons.contains(b.id)
                        || (b.actionKind == GlfwKeys.ACTION_SPECIAL && b.keyCode == GlfwKeys.SPECIAL_CURSOR && cursorMode);
                HudRenderer.drawButton(canvas, tmpRect, b, active, false, false, density, paint);
            }
        } else {
            for (ControlButtonSpec b : profile.buttons) {
                if (b.actionKind == GlfwKeys.ACTION_SPECIAL && b.keyCode == GlfwKeys.SPECIAL_HIDE_HUD) {
                    HudRenderer.computeButtonRect(b, w, h, density, tmpRect);
                    HudRenderer.drawButton(canvas, tmpRect, b, true, false, false, density, paint);
                }
            }
        }

        drawQuickMenuPill(canvas, w, density);
        if (showF3Hud) drawTelemetryHud(canvas, density);
        if (cursorMode) drawVirtualCursor(canvas, cursorNormX * w, cursorNormY * h);
    }

    private void drawQuickMenuPill(Canvas canvas, int w, float density) {
        RectF r = getQuickMenuRect(w, density);
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(0x990A101A);
        canvas.drawRoundRect(r, r.height() * 0.5f, r.height() * 0.5f, paint);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(dp(1.2f));
        paint.setColor(0xAA38BDF8);
        canvas.drawRoundRect(r, r.height() * 0.5f, r.height() * 0.5f, paint);
        paint.setStyle(Paint.Style.FILL);
        paint.setTextAlign(Paint.Align.CENTER);
        paint.setTypeface(Typeface.DEFAULT_BOLD);
        paint.setTextSize(dp(9f));
        paint.setColor(0xFFF1F5F9);
        canvas.drawText("MUFFIN", r.centerX(), r.centerY() - ((paint.descent() + paint.ascent()) * 0.5f), paint);
    }

    private void drawVirtualCursor(Canvas canvas, float cx, float cy) {
        float s = dp(18f);
        cursorPath.reset();
        cursorPath.moveTo(cx, cy); cursorPath.lineTo(cx, cy + s);
        cursorPath.lineTo(cx + s * 0.28f, cy + s * 0.72f);
        cursorPath.lineTo(cx + s * 0.56f, cy + s * 1.10f);
        cursorPath.lineTo(cx + s * 0.72f, cy + s * 0.98f);
        cursorPath.lineTo(cx + s * 0.44f, cy + s * 0.62f);
        cursorPath.lineTo(cx + s * 0.78f, cy + s * 0.56f);
        cursorPath.close();
        paint.setStyle(Paint.Style.FILL); paint.setColor(0xFFFFFFFF); canvas.drawPath(cursorPath, paint);
        paint.setStyle(Paint.Style.STROKE); paint.setStrokeWidth(dp(1.4f)); paint.setColor(0xFF0F172A); canvas.drawPath(cursorPath, paint);
    }

    private void drawTelemetryHud(Canvas canvas, float density) {
        float x = dp(10f), y = dp(8f), boxW = dp(264f), boxH = dp(68f);
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(0x99070B12);
        canvas.drawRoundRect(new RectF(x, y, x + boxW, y + boxH), dp(8f), dp(8f), paint);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(dp(1f));
        paint.setColor(0x8838BDF8);
        canvas.drawRoundRect(new RectF(x, y, x + boxW, y + boxH), dp(8f), dp(8f), paint);

        double fps = telemetry[0] / 10.0;
        String backend = telemetry[12] == 1 ? "Vulkan+GLES" : "GLES3+Mojo";
        double arenaMb = telemetry[2] / (1024.0 * 1024.0);
        double slabMb = telemetry[3] / (1024.0 * 1024.0);
        String line1 = String.format(Locale.US, "%s | %s | %.1f FPS | %d Worker", username, backend, fps, telemetry[11]);
        String line2 = String.format(Locale.US, "Vis:%d Bat:%d Inst:%d Cull(F/P/D):%d/%d/%d",
                telemetry[5], telemetry[9], telemetry[10], telemetry[6], telemetry[7], telemetry[8]);
        String line3 = String.format(Locale.US, "Arena:%.2fMB Slab:%.2fMB XYZ:%.1f/%.1f/%.1f",
                arenaMb, slabMb, telemetry[13] / 10.0, telemetry[14] / 10.0, telemetry[15] / 10.0);
        String line4 = engineModeLabel + (cursorMode ? " | FARE MODU" : "");

        paint.setStyle(Paint.Style.FILL);
        paint.setTextAlign(Paint.Align.LEFT);
        paint.setTypeface(Typeface.MONOSPACE);
        paint.setTextSize(dp(8.6f));
        paint.setColor(0xFF38BDF8); canvas.drawText(line1, x + dp(7f), y + dp(15f), paint);
        paint.setColor(0xFFE2E8F0); canvas.drawText(line2, x + dp(7f), y + dp(29f), paint);
        canvas.drawText(line3, x + dp(7f), y + dp(43f), paint);
        paint.setColor(0xFF3DDC84); canvas.drawText(line4, x + dp(7f), y + dp(57f), paint);
    }

    private void triggerHaptic() {
        if (config.hapticLevel > 0) performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
    }

    private void handleSpecialTap(ControlButtonSpec b) {
        if (b.keyCode == GlfwKeys.SPECIAL_CURSOR) {
            cursorMode = !cursorMode;
            if (NativeBridge.isLoaded()) NativeBridge.nativeSetCursorMode(cursorMode, cursorNormX, cursorNormY);
        } else if (b.keyCode == GlfwKeys.SPECIAL_KEYBOARD) {
            if (callbacks != null) callbacks.onToggleSoftKeyboard();
        } else if (b.keyCode == GlfwKeys.SPECIAL_HIDE_HUD) {
            hudHidden = !hudHidden;
        } else if (b.keyCode == GlfwKeys.SPECIAL_MENU) {
            if (callbacks != null) callbacks.onOpenQuickMenu();
        }
        invalidate();
    }

    @Override
    public boolean onTouchEvent(MotionEvent event) {
        int action = event.getActionMasked(), idx = event.getActionIndex(), pid = event.getPointerId(idx);
        float px = event.getX(idx), py = event.getY(idx);
        int w = getWidth(), h = getHeight();
        float density = getResources().getDisplayMetrics().density;

        switch (action) {
            case MotionEvent.ACTION_DOWN:
            case MotionEvent.ACTION_POINTER_DOWN: {
                if (getQuickMenuRect(w, density).contains(px, py)) {
                    triggerHaptic();
                    if (callbacks != null) callbacks.onOpenQuickMenu();
                    return true;
                }
                if (!hudHidden) {
                    RectF hotbar = HudRenderer.getHotbarRect(w, h, density);
                    int slot = hotbar.contains(px, py) ? HudRenderer.hotbarSlotAt(w, h, density, px) : -1;
                    if (slot >= 0) {
                        selectedHotbar = slot;
                        if (NativeBridge.isLoaded()) {
                            NativeBridge.nativeSelectHotbar(slot);
                            NativeBridge.nativeSetKey(GlfwKeys.KEY_1 + slot, true);
                            NativeBridge.nativeSetKey(GlfwKeys.KEY_1 + slot, false);
                        }
                        triggerHaptic(); invalidate(); return true;
                    }
                }
                if (profile != null) {
                    for (int i = profile.buttons.size() - 1; i >= 0; i--) {
                        ControlButtonSpec b = profile.buttons.get(i);
                        if (!b.visible || (hudHidden && !(b.actionKind == GlfwKeys.ACTION_SPECIAL && b.keyCode == GlfwKeys.SPECIAL_HIDE_HUD))) continue;
                        HudRenderer.computeButtonRect(b, w, h, density, tmpRect);
                        tmpRect.inset(-dp(5f), -dp(5f));
                        if (tmpRect.contains(px, py)) {
                            pointerButtonMap.put(pid, b.id);
                            pointerLastXY.put(pid, new float[]{px, py});
                            triggerHaptic();
                            if (b.actionKind == GlfwKeys.ACTION_SPECIAL) handleSpecialTap(b);
                            else if (b.toggleMode) {
                                boolean nowActive = !toggledButtons.contains(b.id);
                                if (nowActive) toggledButtons.add(b.id); else toggledButtons.remove(b.id);
                                GlfwKeys.dispatch(b.actionKind, b.keyCode, nowActive);
                            } else {
                                heldButtons.add(b.id);
                                GlfwKeys.dispatch(b.actionKind, b.keyCode, true);
                                if (b.actionKind == GlfwKeys.ACTION_KEY && b.keyCode == GlfwKeys.KEY_F3) toggleF3Hud();
                            }
                            invalidate(); return true;
                        }
                    }
                }
                if (!hudHidden && profile != null && stickPointerId == -1) {
                    float stickCx = profile.joystickXPct * w, stickCy = profile.joystickYPct * h;
                    float stickR = (profile.joystickSizeDp * 0.55f) * density;
                    boolean hitStick = Math.hypot(px - stickCx, py - stickCy) <= stickR;
                    boolean dynamicZone = profile.dynamicJoystick && px < w * 0.45f && py > h * 0.25f;
                    if (hitStick || dynamicZone) {
                        stickPointerId = pid;
                        stickOriginX = dynamicZone ? px : stickCx;
                        stickOriginY = dynamicZone ? py : stickCy;
                        updateStickPosition(px, py, density);
                        return true;
                    }
                }
                if (cursorMode && cursorPointerId == -1) {
                    cursorPointerId = pid; cursorDownX = px; cursorDownY = py;
                    cursorDragged = false; lastLookX = px; lastLookY = py;
                    return true;
                }
                if (lookPointerId == -1) {
                    lookPointerId = pid; lastLookX = px; lastLookY = py;
                    return true;
                }
                return true;
            }
            case MotionEvent.ACTION_MOVE: {
                for (int i = 0; i < event.getPointerCount(); i++) {
                    int mpid = event.getPointerId(i);
                    float mx = event.getX(i), my = event.getY(i);
                    if (mpid == stickPointerId) updateStickPosition(mx, my, density);
                    else if (mpid == cursorPointerId && cursorMode) {
                        float dx = mx - lastLookX, dy = my - lastLookY;
                        lastLookX = mx; lastLookY = my;
                        if (Math.hypot(mx - cursorDownX, my - cursorDownY) > dp(6f)) cursorDragged = true;
                        cursorNormX = Math.max(0f, Math.min(1f, cursorNormX + dx / Math.max(1f, w)));
                        cursorNormY = Math.max(0f, Math.min(1f, cursorNormY + dy / Math.max(1f, h)));
                        if (NativeBridge.isLoaded()) NativeBridge.nativeSetCursorMode(true, cursorNormX, cursorNormY);
                        invalidate();
                    } else if (mpid == lookPointerId) {
                        float dx = mx - lastLookX, dy = my - lastLookY;
                        lastLookX = mx; lastLookY = my;
                        if (NativeBridge.isLoaded()) NativeBridge.nativeAddLookDelta(dx, dy);
                    } else {
                        String btnId = pointerButtonMap.get(mpid);
                        if (btnId != null) {
                            ControlButtonSpec b = findButton(btnId);
                            float[] prev = pointerLastXY.get(mpid);
                            if (b != null && b.swipeable && prev != null) {
                                if (NativeBridge.isLoaded()) NativeBridge.nativeAddLookDelta(mx - prev[0], my - prev[1]);
                                prev[0] = mx; prev[1] = my;
                            }
                        }
                    }
                }
                return true;
            }
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_POINTER_UP:
            case MotionEvent.ACTION_CANCEL: {
                if (action == MotionEvent.ACTION_CANCEL) { releaseAllInputs(); return true; }
                pointerLastXY.remove(pid);
                String btnId = pointerButtonMap.remove(pid);
                if (btnId != null) {
                    ControlButtonSpec b = findButton(btnId);
                    if (b != null && b.actionKind != GlfwKeys.ACTION_SPECIAL && !b.toggleMode) {
                        heldButtons.remove(btnId);
                        GlfwKeys.dispatch(b.actionKind, b.keyCode, false);
                    }
                    invalidate();
                } else if (pid == stickPointerId) {
                    stickPointerId = -1; currentMoveX = 0f; currentMoveY = 0f;
                    if (NativeBridge.isLoaded()) NativeBridge.nativeSetMoveAxis(0f, 0f);
                    invalidate();
                } else if (pid == cursorPointerId) {
                    cursorPointerId = -1;
                    if (!cursorDragged && NativeBridge.isLoaded()) {
                        NativeBridge.nativeMouseButton(GlfwKeys.MOUSE_LEFT, true);
                        NativeBridge.nativeMouseButton(GlfwKeys.MOUSE_LEFT, false);
                    }
                } else if (pid == lookPointerId) lookPointerId = -1;
                return true;
            }
        }
        return super.onTouchEvent(event);
    }

    private void updateStickPosition(float px, float py, float density) {
        if (profile == null) return;
        float radius = (profile.joystickSizeDp * 0.5f) * density;
        float dx = px - stickOriginX, dy = py - stickOriginY, dist = (float) Math.hypot(dx, dy);
        if (dist > radius && dist > 0.001f) { dx = (dx / dist) * radius; dy = (dy / dist) * radius; }
        stickThumbX = stickOriginX + dx; stickThumbY = stickOriginY + dy;
        float nx = dx / Math.max(1f, radius), ny = -dy / Math.max(1f, radius);
        if (profile.joystickMode == ControlProfile.STICK_DPAD) {
            nx = Math.abs(nx) > 0.28f ? Math.signum(nx) : 0f;
            ny = Math.abs(ny) > 0.28f ? Math.signum(ny) : 0f;
        } else {
            if (Math.abs(nx) < 0.10f) nx = 0f;
            if (Math.abs(ny) < 0.10f) ny = 0f;
        }
        currentMoveX = nx; currentMoveY = ny;
        if (NativeBridge.isLoaded()) NativeBridge.nativeSetMoveAxis(nx, ny);
        invalidate();
    }
}
