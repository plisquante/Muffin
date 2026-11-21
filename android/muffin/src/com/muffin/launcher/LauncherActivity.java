package com.muffin.launcher;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.CompoundButton;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import java.util.Random;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

public final class LauncherActivity extends Activity {
    private static final int REQ_EXPORT_REPORTS = 4002;

    private LauncherConfig config;
    private ControlProfile activeProfile;
    private MojangDownloader.VersionEntry chosenOnlineVersion = null;

    private FrameLayout contentHost;
    private final Button[] tabButtons = new Button[4];
    private int currentTab = 0;

    private SkinAvatarView avatarView;
    private EditText usernameInput;
    private TextView usernameStatus;
    private TextView checklistText;
    private TextView engineModeBadge;
    private Button mainPlayBtn;

    private TextView probeOutputText;
    private TextView logOutputText;
    private TextView crashOutputText;

    private static final int COL_BG = 0xFF070B12;
    private static final int COL_HEADER = 0xFF0B121F;
    private static final int COL_CARD = 0xFF101929;
    private static final int COL_SURFACE = 0xFF172338;
    private static final int COL_BORDER = 0xFF263B59;
    private static final int COL_MOJO = 0xFF38BDF8;
    private static final int COL_EMERALD = 0xFF22C55E;
    private static final int COL_TEXT = 0xFFF1F5F9;
    private static final int COL_MUTED = 0xFF94A3B8;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        LauncherConfig.installCrashHandler(this);
        config = new LauncherConfig(this);
        activeProfile = config.loadActiveProfile();

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(COL_BG);
        root.setFitsSystemWindows(true);

        root.addView(buildHeader());
        root.addView(buildTabBar());

        contentHost = new FrameLayout(this);
        contentHost.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        root.addView(contentHost);

        setContentView(root);
        selectTab(0);
    }

    @Override
    protected void onResume() {
        super.onResume();
        config.load();
        activeProfile = config.loadActiveProfile();
        selectTab(currentTab);
    }

    private int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }

    private GradientDrawable roundedBox(int fill, int stroke, int radiusDp) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(fill); d.setStroke(dp(1), stroke); d.setCornerRadius(dp(radiusDp));
        return d;
    }

    private View buildHeader() {
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(dp(12), dp(8), dp(12), dp(8));
        header.setBackgroundColor(COL_HEADER);

        ImageView logo = new ImageView(this);
        logo.setImageResource(R.drawable.mojo_edition_banner);
        logo.setScaleType(ImageView.ScaleType.FIT_START);
        LinearLayout.LayoutParams logoLp = new LinearLayout.LayoutParams(dp(186), dp(36));
        logoLp.rightMargin = dp(8);
        header.addView(logo, logoLp);

        LinearLayout rightCol = new LinearLayout(this);
        rightCol.setOrientation(LinearLayout.VERTICAL);
        rightCol.setGravity(Gravity.END | Gravity.CENTER_VERTICAL);
        rightCol.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        TextView title = new TextView(this);
        title.setText("Muffin Launcher");
        title.setTextColor(COL_TEXT);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setTextSize(13.5f);
        title.setGravity(Gravity.END);

        TextView badge = new TextView(this);
        badge.setText("MOJO 1.2 ZERO-GC • ARM64/x64");
        badge.setTextColor(COL_MOJO);
        badge.setTextSize(9.5f);
        badge.setTypeface(Typeface.MONOSPACE);
        badge.setGravity(Gravity.END);

        rightCol.addView(title);
        rightCol.addView(badge);
        header.addView(rightCol);
        return header;
    }

    private View buildTabBar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setBackgroundColor(0xFF090F1A);
        bar.setPadding(dp(10), dp(6), dp(10), dp(8));

        String[] labels = {"OYNA", "KONTROLLER", "AYARLAR", "SİSTEM"};
        for (int i = 0; i < labels.length; i++) {
            final int tabIdx = i;
            Button b = new Button(this);
            b.setText(labels[i]);
            b.setTextSize(11.5f);
            b.setTypeface(Typeface.DEFAULT_BOLD);
            b.setAllCaps(false);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(36), 1f);
            lp.setMargins(dp(3), 0, dp(3), 0);
            b.setLayoutParams(lp);
            b.setOnClickListener(new View.OnClickListener() {
                @Override public void onClick(View v) { saveTextInputsIfPresent(); selectTab(tabIdx); }
            });
            tabButtons[i] = b;
            bar.addView(b);
        }
        return bar;
    }

    private void selectTab(int index) {
        currentTab = index;
        for (int i = 0; i < tabButtons.length; i++) {
            boolean active = (i == index);
            tabButtons[i].setBackground(roundedBox(active ? 0xFF0C4A6E : COL_CARD, active ? COL_MOJO : COL_BORDER, 10));
            tabButtons[i].setTextColor(active ? 0xFFFFFFFF : COL_MUTED);
        }
        contentHost.removeAllViews();
        ScrollView scroll = new ScrollView(this);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(14), dp(10), dp(14), dp(24));
        if (index == 0) buildPlayTab(page);
        else if (index == 1) buildControlsTab(page);
        else if (index == 2) buildSettingsTab(page);
        else buildSystemTab(page);
        scroll.addView(page);
        contentHost.addView(scroll);
    }

    private LinearLayout makeCard(String heading) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(roundedBox(COL_CARD, COL_BORDER, 14));
        card.setPadding(dp(14), dp(12), dp(14), dp(14));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.bottomMargin = dp(12);
        card.setLayoutParams(lp);
        if (heading != null && !heading.isEmpty()) {
            TextView h = new TextView(this);
            h.setText(heading);
            h.setTextColor(COL_MOJO);
            h.setTextSize(11.5f);
            h.setTypeface(Typeface.DEFAULT_BOLD);
            h.setPadding(0, 0, 0, dp(8));
            card.addView(h);
        }
        return card;
    }

    private Button makeStyledBtn(String text, int bg, int border) {
        Button b = new Button(this);
        b.setText(text);
        b.setTextColor(0xFFFFFFFF);
        b.setTextSize(12.5f);
        b.setAllCaps(false);
        b.setTypeface(Typeface.DEFAULT_BOLD);
        b.setBackground(roundedBox(bg, border, 10));
        b.setPadding(dp(12), dp(6), dp(12), dp(6));
        return b;
    }

    private void saveTextInputsIfPresent() {
        if (usernameInput != null) {
            String candidate = LauncherConfig.sanitizeUsername(usernameInput.getText().toString().trim());
            if (LauncherConfig.isValidUsername(candidate)) {
                config.username = candidate;
                config.save();
            }
        }
    }

    private void buildPlayTab(LinearLayout page) {
        // Hero Banner Card with attached MINECRAFT MOJO EDITION logo
        LinearLayout heroCard = new LinearLayout(this);
        heroCard.setOrientation(LinearLayout.VERTICAL);
        heroCard.setBackground(roundedBox(0xFF0A1424, COL_MOJO, 14));
        heroCard.setPadding(dp(14), dp(12), dp(14), dp(12));
        LinearLayout.LayoutParams hLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        hLp.bottomMargin = dp(12);
        heroCard.setLayoutParams(hLp);

        ImageView heroLogo = new ImageView(this);
        heroLogo.setImageResource(R.drawable.mojo_edition_banner);
        heroLogo.setScaleType(ImageView.ScaleType.FIT_CENTER);
        heroCard.addView(heroLogo, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(58)));

        TextView heroSub = new TextView(this);
        heroSub.setText("Zero-GC ARC • Multi-Core WorkerPool • Tek Tıkla %100 İndir & Başlat");
        heroSub.setTextColor(COL_MUTED);
        heroSub.setTextSize(11f);
        heroSub.setGravity(Gravity.CENTER);
        heroSub.setPadding(0, dp(4), 0, 0);
        heroCard.addView(heroSub);
        page.addView(heroCard);

        // Player Profile
        LinearLayout userCard = makeCard("OYUNCU PROFİLİ");
        LinearLayout userRow = new LinearLayout(this);
        userRow.setOrientation(LinearLayout.HORIZONTAL);
        userRow.setGravity(Gravity.CENTER_VERTICAL);

        avatarView = new SkinAvatarView(this);
        avatarView.setUsername(config.username);
        LinearLayout.LayoutParams avLp = new LinearLayout.LayoutParams(dp(58), dp(58));
        avLp.rightMargin = dp(12);
        userRow.addView(avatarView, avLp);

        LinearLayout inputCol = new LinearLayout(this);
        inputCol.setOrientation(LinearLayout.VERTICAL);
        inputCol.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        usernameInput = new EditText(this);
        usernameInput.setText(config.username);
        usernameInput.setHint("Oyuncu Adı (3-16 karakter)");
        usernameInput.setHintTextColor(COL_MUTED);
        usernameInput.setTextColor(COL_TEXT);
        usernameInput.setTextSize(14f);
        usernameInput.setSingleLine(true);
        usernameInput.setBackground(roundedBox(COL_BG, COL_BORDER, 9));
        usernameInput.setPadding(dp(10), dp(8), dp(10), dp(8));

        usernameStatus = new TextView(this);
        usernameStatus.setTextSize(11f);
        usernameStatus.setPadding(0, dp(4), 0, 0);
        inputCol.addView(usernameInput);
        inputCol.addView(usernameStatus);
        userRow.addView(inputCol);

        Button randBtn = makeStyledBtn("Zar", COL_SURFACE, COL_BORDER);
        LinearLayout.LayoutParams randLp = new LinearLayout.LayoutParams(dp(54), dp(40));
        randLp.leftMargin = dp(8);
        randBtn.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { usernameInput.setText("Mojo_" + (100 + new Random().nextInt(8999))); }
        });
        userRow.addView(randBtn, randLp);
        userCard.addView(userRow);

        usernameInput.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            @Override public void onTextChanged(CharSequence s, int start, int before, int count) {}
            @Override public void afterTextChanged(Editable s) { validateAndRefreshUi(s.toString().trim()); }
        });
        page.addView(userCard);

        // 100% Complete Download & Engine Status Card
        LinearLayout modeCard = makeCard("%100 KURULUM, SÜRÜM & MOTOR DURUMU");
        engineModeBadge = new TextView(this);
        engineModeBadge.setTextSize(12f);
        engineModeBadge.setTypeface(Typeface.DEFAULT_BOLD);
        engineModeBadge.setPadding(dp(10), dp(8), dp(10), dp(8));
        modeCard.addView(engineModeBadge);

        checklistText = new TextView(this);
        checklistText.setTextColor(COL_TEXT);
        checklistText.setTextSize(11.5f);
        checklistText.setTypeface(Typeface.MONOSPACE);
        checklistText.setLineSpacing(dp(3), 1f);
        checklistText.setPadding(dp(4), dp(8), dp(4), dp(4));
        modeCard.addView(checklistText);

        LinearLayout modeBtns = new LinearLayout(this);
        modeBtns.setOrientation(LinearLayout.HORIZONTAL);
        modeBtns.setPadding(0, dp(6), 0, 0);

        Button pickVerBtn = makeStyledBtn("Sürüm Seç", 0xFF0C4A6E, COL_MOJO);
        pickVerBtn.setLayoutParams(new LinearLayout.LayoutParams(0, dp(40), 1f));
        pickVerBtn.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View v) { openVersionPicker(); } });

        Button forceDlBtn = makeStyledBtn("%100 Doğrula / İndir", 0xFF0F766E, 0xFF2DD4BF);
        LinearLayout.LayoutParams dlLp = new LinearLayout.LayoutParams(0, dp(40), 1f);
        dlLp.leftMargin = dp(8);
        forceDlBtn.setLayoutParams(dlLp);
        forceDlBtn.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View v) { runFullInstallAndLaunch(false); } });

        modeBtns.addView(pickVerBtn);
        modeBtns.addView(forceDlBtn);
        modeCard.addView(modeBtns);
        page.addView(modeCard);

        LinearLayout quickCard = makeCard("KONTROLLER & HIZLI ERİŞİM");
        LinearLayout quickRow = new LinearLayout(this);
        quickRow.setOrientation(LinearLayout.HORIZONTAL);
        Button hudBtn = makeStyledBtn("HUD Tuş Editörü", 0xFF1E3A8A, COL_MOJO);
        hudBtn.setLayoutParams(new LinearLayout.LayoutParams(0, dp(40), 1f));
        hudBtn.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                saveTextInputsIfPresent();
                Intent i = new Intent(LauncherActivity.this, ControlsEditorActivity.class);
                i.putExtra("profile_name", config.activeProfileName);
                startActivity(i);
            }
        });
        Button gfxBtn = makeStyledBtn("Grafik / RHI / FPS", COL_SURFACE, COL_BORDER);
        LinearLayout.LayoutParams gLp = new LinearLayout.LayoutParams(0, dp(40), 1f);
        gLp.leftMargin = dp(8);
        gfxBtn.setLayoutParams(gLp);
        gfxBtn.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View v) { saveTextInputsIfPresent(); selectTab(2); } });
        quickRow.addView(hudBtn); quickRow.addView(gfxBtn);
        quickCard.addView(quickRow);
        page.addView(quickCard);

        mainPlayBtn = makeStyledBtn("▶  %100 İNDİR & OYUNU BAŞLAT", 0xFF0284C7, COL_MOJO);
        mainPlayBtn.setTextSize(15f);
        LinearLayout.LayoutParams playLp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(58));
        playLp.topMargin = dp(4);
        mainPlayBtn.setLayoutParams(playLp);
        mainPlayBtn.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                String raw = usernameInput.getText().toString().trim();
                if (!LauncherConfig.isValidUsername(raw)) {
                    Toast.makeText(LauncherActivity.this, "Geçerli bir oyuncu adı girin (3-16 karakter: A-Z, 0-9, _).", Toast.LENGTH_LONG).show();
                    return;
                }
                config.username = raw; config.save();
                if (config.isSetupComplete()) {
                    prepareAndLaunchGame();
                } else {
                    runFullInstallAndLaunch(true);
                }
            }
        });
        page.addView(mainPlayBtn);
        validateAndRefreshUi(config.username);
    }

    private void validateAndRefreshUi(String text) {
        boolean ok = LauncherConfig.isValidUsername(text);
        if (avatarView != null) avatarView.setUsername(ok ? text : config.username);
        if (usernameStatus != null) {
            if (ok) {
                usernameStatus.setText("Hazır: " + text); usernameStatus.setTextColor(COL_EMERALD); config.username = text;
            } else {
                usernameStatus.setText("3-16 karakter (A-Z, 0-9, _)"); usernameStatus.setTextColor(0xFFFCA5A5);
            }
        }
        boolean jarOk = config.clientJarPath != null && new File(config.clientJarPath).isFile();
        boolean resOk = new File(config.resourcesDir, "assets/minecraft/textures/block").isDirectory();
        boolean assetsOk = new File(config.assetsDir, "indexes").isDirectory();
        boolean libsOk = config.librariesRoot().isDirectory() && !config.completeVersionStamp.isEmpty();
        boolean mojoNativeOk = NativeBridge.isLoaded();

        if (checklistText != null) {
            checklistText.setText(String.format(Locale.US,
                    "[%s] Sürüm JAR: %s\n[%s] Java Kütüphaneleri\n[%s] Asset Paketi (Ses/Doku)\n[%s] Doku Atlası & Kaynaklar (resources/)\n[%s] Mojo 1.2 Multi-Core Chunk Motoru",
                    jarOk ? "✓" : " ", config.selectedVersion,
                    libsOk ? "✓" : " ",
                    assetsOk ? "✓" : " ",
                    resOk ? "✓" : " ",
                    mojoNativeOk ? "✓" : "!"));
        }
        if (engineModeBadge != null) {
            if (config.isSetupComplete()) {
                engineModeBadge.setText("HAZIR (%100 İNDİRİLDİ) • " + config.selectedVersion);
                engineModeBadge.setTextColor(0xFF4ADE80);
                engineModeBadge.setBackground(roundedBox(0xFF0E291D, 0xFF22C55E, 8));
            } else {
                engineModeBadge.setText("KURULUM GEREKLİ • Oyunu Başlatınca %100 İndirilir");
                engineModeBadge.setTextColor(COL_MOJO);
                engineModeBadge.setBackground(roundedBox(0xFF0A1D33, COL_MOJO, 8));
            }
        }
        if (mainPlayBtn != null) {
            mainPlayBtn.setText(config.isSetupComplete()
                    ? ("▶  OYUNU BAŞLAT (" + config.selectedVersion + ")")
                    : "▶  %100 İNDİR & OYUNU BAŞLAT");
        }
    }

    private void openVersionPicker() {
        Toast.makeText(this, "Mojang sürümleri çekiliyor...", Toast.LENGTH_SHORT).show();
        new Thread(new Runnable() {
            @Override public void run() {
                try {
                    final List<MojangDownloader.VersionEntry> vers = MojangDownloader.fetchVersions(32, config.showSnapshots);
                    runOnUiThread(new Runnable() {
                        @Override public void run() {
                            String[] labels = new String[vers.size()];
                            for (int i = 0; i < vers.size(); i++) labels[i] = vers.get(i).toString();
                            new AlertDialog.Builder(LauncherActivity.this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                                    .setTitle("Mojang Sürümü Seç")
                                    .setItems(labels, new DialogInterface.OnClickListener() {
                                        @Override public void onClick(DialogInterface d, int which) {
                                            chosenOnlineVersion = vers.get(which);
                                            config.selectedVersion = chosenOnlineVersion.id;
                                            config.completeVersionStamp = "";
                                            config.save();
                                            validateAndRefreshUi(config.username);
                                            runFullInstallAndLaunch(false);
                                        }
                                    }).setNegativeButton("İptal", null).show();
                        }
                    });
                } catch (final Throwable t) {
                    runOnUiThread(new Runnable() {
                        @Override public void run() {
                            Toast.makeText(LauncherActivity.this, "Mojang bağlantısı başarısız: " + t.getMessage(), Toast.LENGTH_LONG).show();
                        }
                    });
                }
            }
        }).start();
    }

    private void runFullInstallAndLaunch(final boolean autoLaunchAfter) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(20), dp(14), dp(20), dp(10));
        final TextView msgTv = new TextView(this);
        msgTv.setText("Mojang Piston-Meta sunucusuna bağlanılıyor...");
        msgTv.setTextColor(COL_TEXT); msgTv.setTextSize(12.5f);
        final ProgressBar pbar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        pbar.setMax(100); pbar.setProgress(1);
        LinearLayout.LayoutParams pbLp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(18));
        pbLp.topMargin = dp(10);
        box.addView(msgTv); box.addView(pbar, pbLp);

        final AlertDialog dlg = new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle("Minecraft %100 Kurulum")
                .setView(box).setCancelable(false).show();

        new Thread(new Runnable() {
            @Override public void run() {
                try {
                    MojangDownloader.VersionEntry target = (chosenOnlineVersion != null)
                            ? chosenOnlineVersion
                            : MojangDownloader.resolveTargetVersion(config.selectedVersion, config.showSnapshots);
                    MojangDownloader.ProgressListener pl = new MojangDownloader.ProgressListener() {
                        @Override public void onProgress(final int pct, final String msg) {
                            runOnUiThread(new Runnable() {
                                @Override public void run() { pbar.setProgress(pct); msgTv.setText("%" + pct + " • " + msg); }
                            });
                        }
                    };
                    final MojangDownloader.DownloadResult res = MojangDownloader.downloadCompleteSetup(
                            target, config.versionsRoot(), config.librariesRoot(), new File(config.assetsDir), pl);

                    config.selectedVersion = res.versionId;
                    config.clientJarPath = res.clientJar.getAbsolutePath();

                    EngineResources.prepare(LauncherActivity.this, config, new EngineResources.Progress() {
                        @Override public void status(final String message) {
                            runOnUiThread(new Runnable() {
                                @Override public void run() { pbar.setProgress(97); msgTv.setText("%97 • " + message); }
                            });
                        }
                    });
                    config.completeVersionStamp = res.versionId + ":" + res.clientJar.length();
                    config.save();

                    runOnUiThread(new Runnable() {
                        @Override public void run() {
                            pbar.setProgress(100);
                            dlg.dismiss();
                            validateAndRefreshUi(config.username);
                            Toast.makeText(LauncherActivity.this,
                                    "✓ " + res.versionId + " %100 hazır (" + res.librariesCount + " lib, " + res.assetsCount + " asset)!",
                                    Toast.LENGTH_LONG).show();
                            if (autoLaunchAfter) {
                                startActivity(new Intent(LauncherActivity.this, GameActivity.class));
                            }
                        }
                    });
                } catch (final Throwable t) {
                    runOnUiThread(new Runnable() {
                        @Override public void run() {
                            dlg.dismiss();
                            new AlertDialog.Builder(LauncherActivity.this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                                    .setTitle("İndirme Hatası")
                                    .setMessage("Kurulum tamamlanamadı:\n" + t.getMessage())
                                    .setPositiveButton("Tamam", null)
                                    .setNeutralButton("Doğrudan Motoru Aç", new DialogInterface.OnClickListener() {
                                        @Override public void onClick(DialogInterface d, int w) {
                                            startActivity(new Intent(LauncherActivity.this, GameActivity.class));
                                        }
                                    }).show();
                        }
                    });
                }
            }
        }).start();
    }

    private void prepareAndLaunchGame() {
        final AlertDialog dlg = new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle("Mojo Motoru Hazırlanıyor").setMessage("Kaynaklar doğrulanıyor...").setCancelable(false).show();
        new Thread(new Runnable() {
            @Override public void run() {
                try {
                    EngineResources.prepare(LauncherActivity.this, config, new EngineResources.Progress() {
                        @Override public void status(final String m) {
                            runOnUiThread(new Runnable() { @Override public void run() { dlg.setMessage(m); } });
                        }
                    });
                    runOnUiThread(new Runnable() {
                        @Override public void run() {
                            dlg.dismiss();
                            startActivity(new Intent(LauncherActivity.this, GameActivity.class));
                        }
                    });
                } catch (final Throwable t) {
                    runOnUiThread(new Runnable() {
                        @Override public void run() {
                            dlg.dismiss();
                            Toast.makeText(LauncherActivity.this, "Başlatma hatası: " + t.getMessage(), Toast.LENGTH_LONG).show();
                        }
                    });
                }
            }
        }).start();
    }

    private void buildControlsTab(LinearLayout page) {
        LinearLayout profCard = makeCard("KONTROL PROFİLİ & HUD EDİTÖRÜ");
        Button pickProfBtn = makeStyledBtn("Aktif Profil: " + config.activeProfileName + "  ▾", COL_SURFACE, COL_MOJO);
        pickProfBtn.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                final String[] arr = config.listProfileNames().toArray(new String[0]);
                new AlertDialog.Builder(LauncherActivity.this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                        .setTitle("Kontrol Profili Seç").setItems(arr, new DialogInterface.OnClickListener() {
                            @Override public void onClick(DialogInterface d, int w) {
                                config.activeProfileName = arr[w]; config.save();
                                activeProfile = config.loadActiveProfile(); selectTab(1);
                            }
                        }).show();
            }
        });
        profCard.addView(pickProfBtn);

        Button edBtn = makeStyledBtn("GÖRSEL HUD EDİTÖRÜNÜ AÇ", 0xFF0369A1, COL_MOJO);
        LinearLayout.LayoutParams elp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(46));
        elp.topMargin = dp(8);
        edBtn.setLayoutParams(elp);
        edBtn.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                Intent i = new Intent(LauncherActivity.this, ControlsEditorActivity.class);
                i.putExtra("profile_name", config.activeProfileName);
                startActivity(i);
            }
        });
        profCard.addView(edBtn);
        page.addView(profCard);

        LinearLayout feelCard = makeCard("HAREKET & HASSASİYET");
        addOptionGroup(feelCard, "Hareket Kontrolü:", new String[]{"Çapraz (+) D-Pad", "Analog Joystick"},
                activeProfile.joystickMode == ControlProfile.STICK_DPAD ? 0 : 1, new OptionChosen() {
                    @Override public void onPick(int idx) {
                        activeProfile.joystickMode = (idx == 0) ? ControlProfile.STICK_DPAD : ControlProfile.STICK_ANALOG;
                        config.saveProfile(activeProfile);
                    }
                });
        addStyledSwitch(feelCard, "Dinamik Sol Dokunuş Joystick", activeProfile.dynamicJoystick, new CompoundButton.OnCheckedChangeListener() {
            @Override public void onCheckedChanged(CompoundButton b, boolean c) { activeProfile.dynamicJoystick = c; config.saveProfile(activeProfile); }
        });
        addStyledSwitch(feelCard, "Ters Y Ekseni", config.invertY, new CompoundButton.OnCheckedChangeListener() {
            @Override public void onCheckedChanged(CompoundButton b, boolean c) { config.invertY = c; config.save(); }
        });
        addStyledSwitch(feelCard, "Dokunmatik Titreşim", config.hapticLevel > 0, new CompoundButton.OnCheckedChangeListener() {
            @Override public void onCheckedChanged(CompoundButton b, boolean c) { config.hapticLevel = c ? 1 : 0; config.save(); }
        });
        addStyledSwitch(feelCard, "F3 Mojo Telemetry Başlangıçta Açık", config.autoF3, new CompoundButton.OnCheckedChangeListener() {
            @Override public void onCheckedChanged(CompoundButton b, boolean c) { config.autoF3 = c; config.save(); }
        });
        addSliderRow(feelCard, "Kamera Hassasiyeti", 27, Math.round((config.sensitivity - 0.3f) * 10f), new SliderCallback() {
            @Override public String formatValue(int p) { return String.format(Locale.US, "%.1fx", 0.3f + p * 0.1f); }
            @Override public void onChanged(int p) { config.sensitivity = 0.3f + p * 0.1f; config.save(); }
        });
        page.addView(feelCard);
    }

    private void buildSettingsTab(LinearLayout page) {
        LinearLayout gfxCard = makeCard("GRAFİK, RHI BACKEND & MOJO MOTORU");
        addOptionGroup(gfxCard, "Grafik RHI Backend:", new String[]{"Oto (GLES3)", "GLES3 + Vulkan RHI", "GLES 3.2"}, config.backendMode, new OptionChosen() {
            @Override public void onPick(int idx) { config.backendMode = idx; config.save(); }
        });
        addOptionGroup(gfxCard, "Güç & Çekirdek Profili:", new String[]{"Eko", "Dengeli", "Turbo"}, config.powerProfile, new OptionChosen() {
            @Override public void onPick(int idx) { config.powerProfile = idx; config.save(); }
        });
        final int[] fpsValues = {30, 60, 90, 120, 0};
        int fpsIdx = 1;
        for (int i = 0; i < fpsValues.length; i++) if (fpsValues[i] == config.targetFps) fpsIdx = i;
        addOptionGroup(gfxCard, "Hedef FPS:", new String[]{"30", "60", "90", "120", "Max"}, fpsIdx, new OptionChosen() {
            @Override public void onPick(int idx) { config.targetFps = fpsValues[idx]; config.save(); }
        });
        addSliderRow(gfxCard, "Görüş Mesafesi", 20, config.renderDistance - 4, new SliderCallback() {
            @Override public String formatValue(int p) { return (4 + p) + " Chunk"; }
            @Override public void onChanged(int p) { config.renderDistance = 4 + p; config.save(); }
        });
        addSliderRow(gfxCard, "Çözünürlük Ölçeği", 50, config.resScalePct - 50, new SliderCallback() {
            @Override public String formatValue(int p) { return "%" + (50 + p); }
            @Override public void onChanged(int p) { config.resScalePct = 50 + p; config.save(); }
        });
        addSliderRow(gfxCard, "Görüş Açısı (FOV)", 60, Math.round(config.fovDeg - 50f), new SliderCallback() {
            @Override public String formatValue(int p) { return (50 + p) + "°"; }
            @Override public void onChanged(int p) { config.fovDeg = 50f + p; config.save(); }
        });
        addStyledSwitch(gfxCard, "Snapshot Sürümlerini Göster", config.showSnapshots, new CompoundButton.OnCheckedChangeListener() {
            @Override public void onCheckedChanged(CompoundButton b, boolean c) { config.showSnapshots = c; config.save(); }
        });
        addStyledSwitch(gfxCard, "Android RAM Kısıtlamalarını Gevşet (Shizuku)", config.shizukuRam, new CompoundButton.OnCheckedChangeListener() {
            @Override public void onCheckedChanged(final CompoundButton b, boolean c) {
                if (!c) { config.shizukuRam = false; config.save(); return; }
                if (!ShizukuRam.running()) {
                    b.setChecked(false);
                    Toast.makeText(LauncherActivity.this, "Shizuku servisi aktif değil.", Toast.LENGTH_LONG).show();
                    return;
                }
                if (ShizukuRam.granted()) { config.shizukuRam = true; config.save(); return; }
                ShizukuRam.request(new rikka.shizuku.Shizuku.OnRequestPermissionResultListener() {
                    @Override public void onRequestPermissionResult(int req, final int grant) {
                        if (req != ShizukuRam.REQUEST_CODE) return;
                        rikka.shizuku.Shizuku.removeRequestPermissionResultListener(this);
                        runOnUiThread(new Runnable() {
                            @Override public void run() {
                                boolean ok = (grant == android.content.pm.PackageManager.PERMISSION_GRANTED);
                                config.shizukuRam = ok; config.save(); b.setChecked(ok);
                            }
                        });
                    }
                });
            }
        });
        page.addView(gfxCard);
    }

    private void buildSystemTab(LinearLayout page) {
        LinearLayout hwCard = makeCard("MOJO 1.2 DONANIM & GPU TANILAMA");
        probeOutputText = new TextView(this);
        probeOutputText.setTextColor(COL_TEXT); probeOutputText.setTypeface(Typeface.MONOSPACE);
        probeOutputText.setTextSize(11.5f); probeOutputText.setText(NativeBridge.safeProbeHardware());
        hwCard.addView(probeOutputText);

        Button rfBtn = makeStyledBtn("Yenile", 0xFF0C4A6E, COL_MOJO);
        LinearLayout.LayoutParams rlp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(38)); rlp.topMargin = dp(8);
        rfBtn.setLayoutParams(rlp);
        rfBtn.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                probeOutputText.setText(NativeBridge.safeProbeHardware());
                logOutputText.setText(NativeBridge.safeGetLastLog());
                crashOutputText.setText(config.readLastCrash());
            }
        });
        hwCard.addView(rfBtn); page.addView(hwCard);

        LinearLayout logCard = makeCard("MOTOR GÜNLÜĞÜ");
        logOutputText = new TextView(this);
        logOutputText.setTextColor(COL_MUTED); logOutputText.setTypeface(Typeface.MONOSPACE);
        logOutputText.setTextSize(11f); logOutputText.setText(NativeBridge.safeGetLastLog());
        logCard.addView(logOutputText); page.addView(logCard);

        LinearLayout crashCard = makeCard("TANILAMA & ÇÖKME İZİ");
        crashOutputText = new TextView(this);
        crashOutputText.setTextColor(0xFFFCA5A5); crashOutputText.setTypeface(Typeface.MONOSPACE);
        crashOutputText.setTextSize(10.5f); crashOutputText.setText(config.readLastCrash());
        crashCard.addView(crashOutputText);

        Button expBtn = makeStyledBtn("Raporu Dışa Aktar (.zip)", 0xFF065F46, COL_EMERALD);
        LinearLayout.LayoutParams exLp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(38)); exLp.topMargin = dp(8);
        expBtn.setLayoutParams(exLp);
        expBtn.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View v) { startReportExport(); } });
        crashCard.addView(expBtn); page.addView(crashCard);
    }

    private interface OptionChosen { void onPick(int idx); }
    private interface SliderCallback { String formatValue(int progress); void onChanged(int progress); }

    private void addOptionGroup(LinearLayout parent, String title, String[] labels, int selectedIdx, final OptionChosen cb) {
        TextView lbl = new TextView(this); lbl.setText(title); lbl.setTextColor(COL_MUTED);
        lbl.setTextSize(11.5f); lbl.setPadding(0, dp(6), 0, dp(4)); parent.addView(lbl);
        LinearLayout row = new LinearLayout(this); row.setOrientation(LinearLayout.HORIZONTAL);
        final Button[] btns = new Button[labels.length];
        for (int i = 0; i < labels.length; i++) {
            final int idx = i;
            Button b = new Button(this); b.setText(labels[i]); b.setAllCaps(false); b.setTextSize(11.5f); b.setTypeface(Typeface.DEFAULT_BOLD);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(36), 1f);
            if (i > 0) lp.leftMargin = dp(5);
            b.setLayoutParams(lp); btns[i] = b;
            b.setOnClickListener(new View.OnClickListener() {
                @Override public void onClick(View v) {
                    for (int k = 0; k < btns.length; k++) {
                        boolean act = (k == idx);
                        btns[k].setBackground(roundedBox(act ? 0xFF0C4A6E : COL_SURFACE, act ? COL_MOJO : COL_BORDER, 8));
                        btns[k].setTextColor(act ? 0xFFFFFFFF : COL_MUTED);
                    }
                    cb.onPick(idx);
                }
            });
            boolean act = (i == selectedIdx);
            b.setBackground(roundedBox(act ? 0xFF0C4A6E : COL_SURFACE, act ? COL_MOJO : COL_BORDER, 8));
            b.setTextColor(act ? 0xFFFFFFFF : COL_MUTED);
            row.addView(b);
        }
        parent.addView(row);
    }

    private void addStyledSwitch(LinearLayout parent, String text, boolean checked, CompoundButton.OnCheckedChangeListener l) {
        Switch sw = new Switch(this); sw.setText(text); sw.setTextColor(COL_TEXT); sw.setTextSize(12.5f);
        sw.setPadding(0, dp(6), 0, dp(6)); sw.setChecked(checked); sw.setOnCheckedChangeListener(l); parent.addView(sw);
    }

    private void addSliderRow(LinearLayout parent, final String title, int max, int initial, final SliderCallback cb) {
        final TextView lbl = new TextView(this);
        lbl.setText(title + ": " + cb.formatValue(initial)); lbl.setTextColor(COL_TEXT); lbl.setTextSize(12f);
        lbl.setPadding(0, dp(8), 0, dp(2)); parent.addView(lbl);
        SeekBar seek = new SeekBar(this); seek.setMax(max); seek.setProgress(Math.max(0, Math.min(max, initial)));
        seek.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override public void onProgressChanged(SeekBar s, int p, boolean fromUser) {
                lbl.setText(title + ": " + cb.formatValue(p)); if (fromUser) cb.onChanged(p);
            }
            @Override public void onStartTrackingTouch(SeekBar s) {}
            @Override public void onStopTrackingTouch(SeekBar s) {}
        });
        parent.addView(seek);
    }

    private void startReportExport() {
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("application/zip");
        intent.putExtra(Intent.EXTRA_TITLE, "muffin-rapor.zip");
        startActivityForResult(intent, REQ_EXPORT_REPORTS);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQ_EXPORT_REPORTS && resultCode == RESULT_OK && data != null && data.getData() != null) {
            try (OutputStream os = getContentResolver().openOutputStream(data.getData()); ZipOutputStream zip = new ZipOutputStream(os)) {
                for (File f : config.reportFiles()) {
                    zip.putNextEntry(new ZipEntry(f.getName()));
                    try (FileInputStream in = new FileInputStream(f)) {
                        byte[] buf = new byte[16384]; int r;
                        while ((r = in.read(buf)) > 0) zip.write(buf, 0, r);
                    }
                    zip.closeEntry();
                }
                Toast.makeText(this, "Rapor kaydedildi.", Toast.LENGTH_SHORT).show();
            } catch (Throwable t) { Toast.makeText(this, "Hata: " + t.getMessage(), Toast.LENGTH_LONG).show(); }
        }
    }
}
