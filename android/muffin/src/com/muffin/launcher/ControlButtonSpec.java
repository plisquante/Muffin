package com.muffin.launcher;

import org.json.JSONException;
import org.json.JSONObject;

public final class ControlButtonSpec {
    public static final int SHAPE_ROUNDED = 0;
    public static final int SHAPE_CIRCLE = 1;
    public static final int SHAPE_PILL = 2;

    public static final int ICON_TEXT = 0;
    public static final int ICON_SWORD = 1;
    public static final int ICON_CUBE = 2;
    public static final int ICON_JUMP = 3;
    public static final int ICON_SNEAK = 4;
    public static final int ICON_SPRINT = 5;
    public static final int ICON_INV = 6;
    public static final int ICON_DROP = 7;
    public static final int ICON_CHAT = 8;
    public static final int ICON_PAUSE = 9;
    public static final int ICON_CURSOR = 10;
    public static final int ICON_KEYBOARD = 11;
    public static final int ICON_CAMERA = 12;
    public static final int ICON_SWAP = 13;
    public static final int ICON_PICK = 14;
    public static final int ICON_EYE = 15;
    public static final int ICON_MENU = 16;

    public String id;
    public String label;
    public int actionKind;
    public int keyCode;
    public int iconKind;
    public float xPct;
    public float yPct;
    public float widthDp;
    public float heightDp;
    public float opacity;
    public int shape;
    public boolean toggleMode;
    public boolean swipeable;
    public int accentColor;
    public boolean locked;
    public boolean visible;

    public ControlButtonSpec(
            String id, String label, int actionKind, int keyCode, int iconKind,
            float xPct, float yPct, float widthDp, float heightDp, float opacity,
            int shape, boolean toggleMode, boolean swipeable, int accentColor,
            boolean locked, boolean visible) {
        this.id = id;
        this.label = label;
        this.actionKind = actionKind;
        this.keyCode = keyCode;
        this.iconKind = iconKind;
        this.xPct = clamp(xPct, 0.03f, 0.97f);
        this.yPct = clamp(yPct, 0.05f, 0.95f);
        this.widthDp = clamp(widthDp, 28f, 140f);
        this.heightDp = clamp(heightDp, 28f, 140f);
        this.opacity = clamp(opacity, 0.15f, 1.0f);
        this.shape = (shape >= 0 && shape <= 2) ? shape : SHAPE_ROUNDED;
        this.toggleMode = toggleMode;
        this.swipeable = swipeable;
        this.accentColor = accentColor;
        this.locked = locked;
        this.visible = visible;
    }

    public ControlButtonSpec copy() {
        return new ControlButtonSpec(
                id, label, actionKind, keyCode, iconKind,
                xPct, yPct, widthDp, heightDp, opacity, shape,
                toggleMode, swipeable, accentColor, locked, visible);
    }

    public JSONObject toJson() throws JSONException {
        JSONObject o = new JSONObject();
        o.put("id", id);
        o.put("label", label);
        o.put("actionKind", actionKind);
        o.put("keyCode", keyCode);
        o.put("iconKind", iconKind);
        o.put("xPct", xPct);
        o.put("yPct", yPct);
        o.put("widthDp", widthDp);
        o.put("heightDp", heightDp);
        o.put("opacity", opacity);
        o.put("shape", shape);
        o.put("toggleMode", toggleMode);
        o.put("swipeable", swipeable);
        o.put("accentColor", accentColor);
        o.put("locked", locked);
        o.put("visible", visible);
        return o;
    }

    public static ControlButtonSpec fromJson(JSONObject o) throws JSONException {
        float fallbackSize = (float) o.optDouble("sizeDp", 54.0);
        return new ControlButtonSpec(
                o.optString("id", "btn_" + System.nanoTime()),
                o.optString("label", "TUŞ"),
                o.optInt("actionKind", GlfwKeys.ACTION_KEY),
                o.optInt("keyCode", GlfwKeys.KEY_SPACE),
                o.optInt("iconKind", ICON_TEXT),
                (float) o.optDouble("xPct", 0.5),
                (float) o.optDouble("yPct", 0.5),
                (float) o.optDouble("widthDp", fallbackSize),
                (float) o.optDouble("heightDp", fallbackSize),
                (float) o.optDouble("opacity", 0.72),
                o.optInt("shape", SHAPE_ROUNDED),
                o.optBoolean("toggleMode", false),
                o.optBoolean("swipeable", false),
                o.optInt("accentColor", 0xFFFFFFFF),
                o.optBoolean("locked", false),
                o.optBoolean("visible", true));
    }

    public static float clamp(float v, float min, float max) {
        return Math.max(min, Math.min(max, v));
    }
}
