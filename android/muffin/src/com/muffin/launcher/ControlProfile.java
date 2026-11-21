package com.muffin.launcher;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

public final class ControlProfile {
    public static final String PRESET_DEFAULT = "Muffin Pro (D-Pad)";
    public static final String PRESET_ANALOG = "Muffin Analog";
    public static final String PRESET_PVP = "PvP 4-Parmak Claw";
    public static final String PRESET_MINIMAL = "Minimal Temiz";

    public static final int STICK_ANALOG = 0;
    public static final int STICK_DPAD = 1;

    public String name;
    public int joystickMode;
    public boolean dynamicJoystick;
    public float joystickXPct;
    public float joystickYPct;
    public float joystickSizeDp;
    public float joystickOpacity;
    public final List<ControlButtonSpec> buttons = new ArrayList<ControlButtonSpec>();

    public ControlProfile(String name) {
        this.name = name;
        this.joystickMode = STICK_DPAD;
        this.dynamicJoystick = false;
        this.joystickXPct = 0.145f;
        this.joystickYPct = 0.69f;
        this.joystickSizeDp = 152f;
        this.joystickOpacity = 0.75f;
    }

    public ControlProfile copy(String newName) {
        ControlProfile p = new ControlProfile(newName);
        p.joystickMode = this.joystickMode;
        p.dynamicJoystick = this.dynamicJoystick;
        p.joystickXPct = this.joystickXPct;
        p.joystickYPct = this.joystickYPct;
        p.joystickSizeDp = this.joystickSizeDp;
        p.joystickOpacity = this.joystickOpacity;
        for (ControlButtonSpec b : this.buttons) p.buttons.add(b.copy());
        return p;
    }

    public JSONObject toJson() throws JSONException {
        JSONObject o = new JSONObject();
        o.put("name", name);
        o.put("joystickMode", joystickMode);
        o.put("dynamicJoystick", dynamicJoystick);
        o.put("joystickXPct", joystickXPct);
        o.put("joystickYPct", joystickYPct);
        o.put("joystickSizeDp", joystickSizeDp);
        o.put("joystickOpacity", joystickOpacity);
        JSONArray arr = new JSONArray();
        for (ControlButtonSpec b : buttons) arr.put(b.toJson());
        o.put("buttons", arr);
        return o;
    }

    public String toPrettyJson() throws JSONException {
        return toJson().toString(2);
    }

    public static ControlProfile fromJson(JSONObject o) throws JSONException {
        ControlProfile p = new ControlProfile(o.optString("name", PRESET_DEFAULT));
        p.joystickMode = o.optInt("joystickMode", STICK_DPAD);
        p.dynamicJoystick = o.optBoolean("dynamicJoystick", false);
        p.joystickXPct = ControlButtonSpec.clamp((float) o.optDouble("joystickXPct", 0.145), 0.06f, 0.45f);
        p.joystickYPct = ControlButtonSpec.clamp((float) o.optDouble("joystickYPct", 0.69), 0.20f, 0.90f);
        p.joystickSizeDp = ControlButtonSpec.clamp((float) o.optDouble("joystickSizeDp", 152.0), 86f, 220f);
        p.joystickOpacity = ControlButtonSpec.clamp((float) o.optDouble("joystickOpacity", 0.75), 0.15f, 1.0f);
        JSONArray arr = o.optJSONArray("buttons");
        if (arr != null) {
            for (int i = 0; i < arr.length(); i++) {
                JSONObject item = arr.optJSONObject(i);
                if (item != null) p.buttons.add(ControlButtonSpec.fromJson(item));
            }
        }
        return p;
    }

    public static ControlProfile fromJsonString(String raw) throws JSONException {
        return fromJson(new JSONObject(raw));
    }

    public static String[] builtInNames() {
        return new String[] {PRESET_DEFAULT, PRESET_ANALOG, PRESET_PVP, PRESET_MINIMAL};
    }

    public static ControlProfile createPreset(String presetName) {
        if (PRESET_ANALOG.equals(presetName)) {
            ControlProfile p = buildDefault().copy(PRESET_ANALOG);
            p.joystickMode = STICK_ANALOG;
            p.joystickSizeDp = 132f;
            return p;
        }
        if (PRESET_PVP.equals(presetName)) return buildPvpPreset();
        if (PRESET_MINIMAL.equals(presetName)) return buildMinimalPreset();
        return buildDefault();
    }

    private static ControlProfile buildDefault() {
        ControlProfile p = new ControlProfile(PRESET_DEFAULT);
        p.buttons.add(new ControlButtonSpec("jump", "ZIPLA", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_SPACE,
                ControlButtonSpec.ICON_JUMP, 0.90f, 0.74f, 62f, 62f, 0.78f,
                ControlButtonSpec.SHAPE_ROUNDED, false, false, 0xFFFFFFFF, false, true));
        p.buttons.add(new ControlButtonSpec("attack", "KIR", GlfwKeys.ACTION_MOUSE, GlfwKeys.MOUSE_LEFT,
                ControlButtonSpec.ICON_SWORD, 0.795f, 0.64f, 60f, 60f, 0.80f,
                ControlButtonSpec.SHAPE_ROUNDED, false, true, 0xFFFF8A8A, false, true));
        p.buttons.add(new ControlButtonSpec("use", "KOY", GlfwKeys.ACTION_MOUSE, GlfwKeys.MOUSE_RIGHT,
                ControlButtonSpec.ICON_CUBE, 0.90f, 0.50f, 58f, 58f, 0.80f,
                ControlButtonSpec.SHAPE_ROUNDED, false, true, 0xFF38BDF8, false, true));
        p.buttons.add(new ControlButtonSpec("sneak", "EĞİL", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_LEFT_SHIFT,
                ControlButtonSpec.ICON_SNEAK, 0.795f, 0.83f, 50f, 44f, 0.74f,
                ControlButtonSpec.SHAPE_ROUNDED, true, false, 0xFFFDE047, false, true));
        p.buttons.add(new ControlButtonSpec("inv", "ENV", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_E,
                ControlButtonSpec.ICON_INV, 0.91f, 0.29f, 46f, 46f, 0.72f,
                ControlButtonSpec.SHAPE_ROUNDED, false, false, 0xFFE2E8F0, false, true));
        p.buttons.add(new ControlButtonSpec("sprint", "KOŞ", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_LEFT_CONTROL,
                ControlButtonSpec.ICON_SPRINT, 0.145f, 0.37f, 52f, 36f, 0.74f,
                ControlButtonSpec.SHAPE_PILL, true, false, 0xFF38BDF8, false, true));
        p.buttons.add(new ControlButtonSpec("drop", "AT", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_Q,
                ControlButtonSpec.ICON_DROP, 0.06f, 0.37f, 42f, 36f, 0.70f,
                ControlButtonSpec.SHAPE_ROUNDED, false, false, 0xFFE2E8F0, false, true));
        p.buttons.add(new ControlButtonSpec("offhand", "EL", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_F,
                ControlButtonSpec.ICON_SWAP, 0.23f, 0.37f, 42f, 36f, 0.70f,
                ControlButtonSpec.SHAPE_ROUNDED, false, false, 0xFFE2E8F0, false, true));
        p.buttons.add(new ControlButtonSpec("cursor", "FARE", GlfwKeys.ACTION_SPECIAL, GlfwKeys.SPECIAL_CURSOR,
                ControlButtonSpec.ICON_CURSOR, 0.35f, 0.08f, 46f, 32f, 0.72f,
                ControlButtonSpec.SHAPE_PILL, true, false, 0xFF38BDF8, false, true));
        p.buttons.add(new ControlButtonSpec("kb", "KB", GlfwKeys.ACTION_SPECIAL, GlfwKeys.SPECIAL_KEYBOARD,
                ControlButtonSpec.ICON_KEYBOARD, 0.42f, 0.08f, 42f, 32f, 0.72f,
                ControlButtonSpec.SHAPE_PILL, false, false, 0xFFE2E8F0, false, true));
        p.buttons.add(new ControlButtonSpec("chat", "SOHBET", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_T,
                ControlButtonSpec.ICON_CHAT, 0.49f, 0.08f, 42f, 32f, 0.72f,
                ControlButtonSpec.SHAPE_PILL, false, false, 0xFFE2E8F0, false, true));
        p.buttons.add(new ControlButtonSpec("f3", "F3", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_F3,
                ControlButtonSpec.ICON_TEXT, 0.56f, 0.08f, 40f, 32f, 0.72f,
                ControlButtonSpec.SHAPE_PILL, false, false, 0xFF3DDC84, false, true));
        p.buttons.add(new ControlButtonSpec("f5", "F5", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_F5,
                ControlButtonSpec.ICON_CAMERA, 0.625f, 0.08f, 42f, 32f, 0.72f,
                ControlButtonSpec.SHAPE_PILL, false, false, 0xFFFDE68A, false, true));
        p.buttons.add(new ControlButtonSpec("hide", "GİZLE", GlfwKeys.ACTION_SPECIAL, GlfwKeys.SPECIAL_HIDE_HUD,
                ControlButtonSpec.ICON_EYE, 0.855f, 0.08f, 38f, 32f, 0.70f,
                ControlButtonSpec.SHAPE_ROUNDED, true, false, 0xFFE2E8F0, false, true));
        p.buttons.add(new ControlButtonSpec("esc", "MENÜ", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_ESCAPE,
                ControlButtonSpec.ICON_PAUSE, 0.935f, 0.08f, 42f, 32f, 0.76f,
                ControlButtonSpec.SHAPE_ROUNDED, false, false, 0xFFFFFFFF, false, true));
        return p;
    }

    private static ControlProfile buildPvpPreset() {
        ControlProfile p = new ControlProfile(PRESET_PVP);
        p.joystickMode = STICK_ANALOG;
        p.dynamicJoystick = true;
        p.joystickXPct = 0.14f;
        p.joystickYPct = 0.71f;
        p.joystickSizeDp = 128f;
        p.joystickOpacity = 0.76f;
        p.buttons.add(new ControlButtonSpec("attack", "VUR", GlfwKeys.ACTION_MOUSE, GlfwKeys.MOUSE_LEFT,
                ControlButtonSpec.ICON_SWORD, 0.85f, 0.34f, 68f, 68f, 0.84f,
                ControlButtonSpec.SHAPE_CIRCLE, false, true, 0xFFFF6B6B, false, true));
        p.buttons.add(new ControlButtonSpec("use", "BLOK", GlfwKeys.ACTION_MOUSE, GlfwKeys.MOUSE_RIGHT,
                ControlButtonSpec.ICON_CUBE, 0.91f, 0.57f, 60f, 60f, 0.80f,
                ControlButtonSpec.SHAPE_ROUNDED, false, true, 0xFF38BDF8, false, true));
        p.buttons.add(new ControlButtonSpec("jump", "ZIPLA", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_SPACE,
                ControlButtonSpec.ICON_JUMP, 0.86f, 0.79f, 64f, 64f, 0.82f,
                ControlButtonSpec.SHAPE_ROUNDED, false, false, 0xFFFFFFFF, false, true));
        p.buttons.add(new ControlButtonSpec("sprint", "KOŞ", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_LEFT_CONTROL,
                ControlButtonSpec.ICON_SPRINT, 0.14f, 0.34f, 52f, 38f, 0.80f,
                ControlButtonSpec.SHAPE_PILL, true, false, 0xFF38BDF8, false, true));
        p.buttons.add(new ControlButtonSpec("sneak", "EĞİL", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_LEFT_SHIFT,
                ControlButtonSpec.ICON_SNEAK, 0.74f, 0.81f, 50f, 50f, 0.76f,
                ControlButtonSpec.SHAPE_ROUNDED, false, false, 0xFFFDE047, false, true));
        p.buttons.add(new ControlButtonSpec("inv", "ENV", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_E,
                ControlButtonSpec.ICON_INV, 0.93f, 0.14f, 44f, 44f, 0.74f,
                ControlButtonSpec.SHAPE_ROUNDED, false, false, 0xFFE2E8F0, false, true));
        p.buttons.add(new ControlButtonSpec("esc", "MENÜ", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_ESCAPE,
                ControlButtonSpec.ICON_PAUSE, 0.53f, 0.08f, 42f, 32f, 0.72f,
                ControlButtonSpec.SHAPE_PILL, false, false, 0xFFFFFFFF, false, true));
        return p;
    }

    private static ControlProfile buildMinimalPreset() {
        ControlProfile p = new ControlProfile(PRESET_MINIMAL);
        p.joystickSizeDp = 142f;
        p.joystickOpacity = 0.56f;
        p.buttons.add(new ControlButtonSpec("jump", "ZIPLA", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_SPACE,
                ControlButtonSpec.ICON_JUMP, 0.90f, 0.75f, 58f, 58f, 0.60f,
                ControlButtonSpec.SHAPE_ROUNDED, false, false, 0xFFFFFFFF, false, true));
        p.buttons.add(new ControlButtonSpec("attack", "KIR", GlfwKeys.ACTION_MOUSE, GlfwKeys.MOUSE_LEFT,
                ControlButtonSpec.ICON_SWORD, 0.79f, 0.67f, 54f, 54f, 0.60f,
                ControlButtonSpec.SHAPE_ROUNDED, false, true, 0xFFFFFFFF, false, true));
        p.buttons.add(new ControlButtonSpec("use", "KOY", GlfwKeys.ACTION_MOUSE, GlfwKeys.MOUSE_RIGHT,
                ControlButtonSpec.ICON_CUBE, 0.89f, 0.52f, 54f, 54f, 0.60f,
                ControlButtonSpec.SHAPE_ROUNDED, false, true, 0xFFFFFFFF, false, true));
        p.buttons.add(new ControlButtonSpec("esc", "MENÜ", GlfwKeys.ACTION_KEY, GlfwKeys.KEY_ESCAPE,
                ControlButtonSpec.ICON_PAUSE, 0.94f, 0.08f, 38f, 34f, 0.56f,
                ControlButtonSpec.SHAPE_ROUNDED, false, false, 0xFFFFFFFF, false, true));
        return p;
    }
}
