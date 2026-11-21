package com.muffin.launcher;

public final class GlfwKeys {
    public static final int ACTION_KEY = 0;
    public static final int ACTION_MOUSE = 1;
    public static final int ACTION_SPECIAL = 2;

    public static final int MOUSE_LEFT = 0;
    public static final int MOUSE_RIGHT = 1;
    public static final int MOUSE_MIDDLE = 2;

    public static final int SPECIAL_CURSOR = 9001;
    public static final int SPECIAL_KEYBOARD = 9002;
    public static final int SPECIAL_HIDE_HUD = 9003;
    public static final int SPECIAL_MENU = 9004;

    public static final int KEY_SPACE = 32;
    public static final int KEY_1 = 49;
    public static final int KEY_A = 65;
    public static final int KEY_B = 66;
    public static final int KEY_C = 67;
    public static final int KEY_D = 68;
    public static final int KEY_E = 69;
    public static final int KEY_F = 70;
    public static final int KEY_G = 71;
    public static final int KEY_Q = 81;
    public static final int KEY_R = 82;
    public static final int KEY_S = 83;
    public static final int KEY_T = 84;
    public static final int KEY_W = 87;
    public static final int KEY_X = 88;
    public static final int KEY_Z = 90;

    public static final int KEY_ESCAPE = 256;
    public static final int KEY_ENTER = 257;
    public static final int KEY_TAB = 258;
    public static final int KEY_BACKSPACE = 259;

    public static final int KEY_F1 = 290;
    public static final int KEY_F2 = 291;
    public static final int KEY_F3 = 292;
    public static final int KEY_F5 = 294;

    public static final int KEY_LEFT_SHIFT = 340;
    public static final int KEY_LEFT_CONTROL = 341;

    public static final class ActionChoice {
        public final String title;
        public final String defaultLabel;
        public final int actionKind;
        public final int code;
        public final int iconKind;
        public final boolean defaultSwipeable;

        public ActionChoice(String title, String defaultLabel, int actionKind, int code, int iconKind, boolean defaultSwipeable) {
            this.title = title;
            this.defaultLabel = defaultLabel;
            this.actionKind = actionKind;
            this.code = code;
            this.iconKind = iconKind;
            this.defaultSwipeable = defaultSwipeable;
        }
    }

    public static final ActionChoice[] PRESET_ACTIONS = new ActionChoice[] {
        new ActionChoice("Saldır / Blok Kır (LMB)", "KIR", ACTION_MOUSE, MOUSE_LEFT, ControlButtonSpec.ICON_SWORD, true),
        new ActionChoice("Kullan / Blok Koy (RMB)", "KOY", ACTION_MOUSE, MOUSE_RIGHT, ControlButtonSpec.ICON_CUBE, true),
        new ActionChoice("Blok Seç / Orta Tık (MMB)", "ORTA", ACTION_MOUSE, MOUSE_MIDDLE, ControlButtonSpec.ICON_PICK, false),
        new ActionChoice("Zıpla / Yukarı (SPACE)", "ZIPLA", ACTION_KEY, KEY_SPACE, ControlButtonSpec.ICON_JUMP, false),
        new ActionChoice("Eğil / İn (LSHIFT)", "EĞİL", ACTION_KEY, KEY_LEFT_SHIFT, ControlButtonSpec.ICON_SNEAK, false),
        new ActionChoice("Koş / Sprint (LCTRL)", "KOŞ", ACTION_KEY, KEY_LEFT_CONTROL, ControlButtonSpec.ICON_SPRINT, false),
        new ActionChoice("Envanter (E)", "ENV", ACTION_KEY, KEY_E, ControlButtonSpec.ICON_INV, false),
        new ActionChoice("Eşya At (Q)", "AT", ACTION_KEY, KEY_Q, ControlButtonSpec.ICON_DROP, false),
        new ActionChoice("El Değiştir (F)", "EL", ACTION_KEY, KEY_F, ControlButtonSpec.ICON_SWAP, false),
        new ActionChoice("Sohbet / Komut (T)", "SOHBET", ACTION_KEY, KEY_T, ControlButtonSpec.ICON_CHAT, false),
        new ActionChoice("Duraklat / ESC", "MENÜ", ACTION_KEY, KEY_ESCAPE, ControlButtonSpec.ICON_PAUSE, false),
        new ActionChoice("Sanal Fare Modu (Özel)", "FARE", ACTION_SPECIAL, SPECIAL_CURSOR, ControlButtonSpec.ICON_CURSOR, false),
        new ActionChoice("Ekran Klavyesi Aç (Özel)", "KB", ACTION_SPECIAL, SPECIAL_KEYBOARD, ControlButtonSpec.ICON_KEYBOARD, false),
        new ActionChoice("HUD Gizle / Göster (Özel)", "GİZLE", ACTION_SPECIAL, SPECIAL_HIDE_HUD, ControlButtonSpec.ICON_EYE, false),
        new ActionChoice("Muffin Hızlı Menü (Özel)", "MF", ACTION_SPECIAL, SPECIAL_MENU, ControlButtonSpec.ICON_MENU, false),
        new ActionChoice("Hata Ayıklama HUD (F3)", "F3", ACTION_KEY, KEY_F3, ControlButtonSpec.ICON_TEXT, false),
        new ActionChoice("Kamera Açısı (F5)", "F5", ACTION_KEY, KEY_F5, ControlButtonSpec.ICON_CAMERA, false),
        new ActionChoice("Oyuncu Listesi (TAB)", "TAB", ACTION_KEY, KEY_TAB, ControlButtonSpec.ICON_TEXT, false),
    };

    private GlfwKeys() {}

    public static String describe(int kind, int code) {
        for (ActionChoice c : PRESET_ACTIONS) {
            if (c.actionKind == kind && c.code == code) return c.title;
        }
        return "Tuş #" + code;
    }

    public static void dispatch(int kind, int code, boolean down) {
        if (!NativeBridge.isLoaded()) return;
        if (kind == ACTION_MOUSE) NativeBridge.nativeMouseButton(code, down);
        else if (kind == ACTION_KEY) NativeBridge.nativeSetKey(code, down);
    }
}
