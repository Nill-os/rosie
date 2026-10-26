package app.rosie.assistant;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.View;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.JsPromptResult;
import android.webkit.JsResult;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.URLUtil;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.Toast;

import org.json.JSONObject;

public class MainActivity extends Activity {
    static final String DEFAULT_URL = "";   // your PC's address, e.g. https://my-pc.my-tailnet.ts.net (asked on first start)
    static final int REQ_MIC = 1, REQ_FILE = 2, REQ_WAKE = 3, REQ_PHONE = 4;
    static final String[] PHONE_PERMS = {Manifest.permission.READ_CONTACTS, Manifest.permission.SEND_SMS, Manifest.permission.READ_SMS, Manifest.permission.CALL_PHONE};
    static volatile boolean visible = false;  // board #685 why-release-mic-when-visible
    boolean askedOverlay = false;

    WebView web;
    ValueCallback<Uri[]> fileCallback;
    PermissionRequest pendingMic;
    String pendingJs;
    SharedPreferences prefs;

    String baseUrl() { return prefs.getString("url", DEFAULT_URL); }

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = getSharedPreferences("assistant", MODE_PRIVATE);
        FrameLayout root = new FrameLayout(this);
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setBuiltInZoomControls(false);
        s.setUserAgentString(s.getUserAgentString() + " AssistantApp/1.0");
        CookieManager.getInstance().setAcceptCookie(true);
        web.addJavascriptInterface(new Bridge(), "AndroidBridge");

        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                Uri u = r.getUrl();
                if (sameHost(u)) return false;
                try { startActivity(new Intent(Intent.ACTION_VIEW, u)); } catch (Exception ignored) { }
                return true;
            }
            @Override public void onPageFinished(WebView v, String url) {
                CookieManager.getInstance().flush();  // board #685 why-flush-login-cookies
                if (pendingJs != null && sameHost(Uri.parse(url))) { String js = pendingJs; pendingJs = null; v.evaluateJavascript(js, null); }
            }
            @Override public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                if (r.isForMainFrame()) showOffline(e.getDescription().toString());
            }
        });
        web.setDownloadListener((url, ua, cd, mime, len) -> {  // board #685 why-download-with-login
            try {
                DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
                r.addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url));
                r.setMimeType(mime);
                r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, URLUtil.guessFileName(url, cd, mime));
                ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
                Toast.makeText(this, "Downloading. When it finishes, tap the notification to install.", Toast.LENGTH_LONG).show();
            } catch (Exception e) { Toast.makeText(this, "Download failed: " + e.getMessage(), Toast.LENGTH_LONG).show(); }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onPermissionRequest(final PermissionRequest req) {
                runOnUiThread(() -> {
                    if (!sameHost(req.getOrigin())) { req.deny(); return; }
                    if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) req.grant(req.getResources());
                    else { pendingMic = req; requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC); }
                });
            }
            @Override public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams p) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = cb;
                try { startActivityForResult(p.createIntent(), REQ_FILE); } catch (Exception e) { fileCallback = null; return false; }
                return true;
            }
            @Override public boolean onJsAlert(WebView v, String url, String msg, JsResult r) {
                new AlertDialog.Builder(MainActivity.this).setMessage(msg).setPositiveButton("OK", (d, w) -> r.confirm()).setOnCancelListener(d -> r.confirm()).show();
                return true;
            }
            @Override public boolean onJsConfirm(WebView v, String url, String msg, JsResult r) {
                new AlertDialog.Builder(MainActivity.this).setMessage(msg).setPositiveButton("OK", (d, w) -> r.confirm()).setNegativeButton("Cancel", (d, w) -> r.cancel()).setOnCancelListener(d -> r.cancel()).show();
                return true;
            }
            @Override public boolean onJsPrompt(WebView v, String url, String msg, String def, JsPromptResult r) {
                final EditText in = new EditText(MainActivity.this);
                in.setInputType(InputType.TYPE_CLASS_TEXT);
                in.setText(def == null ? "" : def);
                AlertDialog dlg = new AlertDialog.Builder(MainActivity.this).setMessage(msg).setView(in)
                        .setPositiveButton("OK", (d, w) -> r.confirm(in.getText().toString())).setNegativeButton("Cancel", (d, w) -> r.cancel()).setOnCancelListener(d -> r.cancel()).create();
                dlg.getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_VISIBLE);
                dlg.show();
                in.requestFocus();
                return true;
            }
        });

        handleIntent(getIntent());
        if (b != null) web.restoreState(b); else web.loadUrl(baseUrl());
    }

    boolean sameHost(Uri u) {
        try { return Uri.parse(baseUrl()).getHost().equalsIgnoreCase(u.getHost()); } catch (Exception e) { return false; }
    }

    void handleIntent(Intent i) {
        if (i == null) return;
        String shared = Intent.ACTION_SEND.equals(i.getAction()) ? i.getStringExtra(Intent.EXTRA_TEXT) : null;
        if (shared != null) pendingJs = "location.hash='#/chat'; setTimeout(function(){ t.value=" + JSONObject.quote(shared) + "; t.focus(); }, 500);";
        else if (i.getBooleanExtra("selftest", false)) pendingJs = "location.hash='#/settings'; setTimeout(function(){ window.runSelfTest && window.runSelfTest(); }, 2500);";
        else if (i.getBooleanExtra("train", false)) pendingJs = "location.hash='#/settings'; setTimeout(function(){ window.startTraining && window.startTraining(); }, 2500);";
        else if (i.getBooleanExtra("talk", false)) pendingJs = "location.hash='#/chat'; setTimeout(function(){ if (!talking) toggleTalk(); }, 700);";
    }

    @Override protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        handleIntent(i);
        if (pendingJs != null && web.getUrl() != null && sameHost(Uri.parse(web.getUrl()))) { String js = pendingJs; pendingJs = null; web.evaluateJavascript(js, null); }
    }

    void showOffline(String why) {
        String html = "<body style='background:#000;color:#9ef;font-family:monospace;padding:28px'><h2>Can't reach your assistant</h2>"
                + "<p>" + why.replace("<", "&lt;") + "</p><p>Is Tailscale connected on this phone, and is the PC on?</p>"
                + "<p><button onclick='AndroidBridge.retry()' style='font-size:18px;padding:14px'>Try again</button></p>"
                + "<p><button onclick='AndroidBridge.changeAddress()' style='font-size:18px;padding:14px'>Change address</button></p></body>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    void askAddress() {
        final EditText in = new EditText(this);
        in.setText(baseUrl());
        in.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        new AlertDialog.Builder(this).setTitle("Assistant address").setMessage("The https address of your assistant (the Tailscale link).").setView(in)
                .setPositiveButton("Save", (d, w) -> {
                    String u = in.getText().toString().trim();
                    if (!u.startsWith("https://")) { Toast.makeText(this, "It must start with https://", Toast.LENGTH_LONG).show(); return; }
                    prefs.edit().putString("url", u.replaceAll("/+$", "")).apply();
                    web.loadUrl(baseUrl());
                }).setNegativeButton("Cancel", null).show();
    }

    class Bridge {
        @JavascriptInterface public void keepAwake(final boolean on) {
            runOnUiThread(() -> { if (on) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON); else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON); });
        }
        @JavascriptInterface public String wakeStatus() {
            try {
                return new JSONObject().put("on", prefs.getBoolean("wake", false)).put("running", WakeService.running)
                        .put("mic", checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED)
                        .put("notif", Build.VERSION.SDK_INT < 33 || checkSelfPermission("android.permission.POST_NOTIFICATIONS") == PackageManager.PERMISSION_GRANTED)
                        .put("overlay", Settings.canDrawOverlays(MainActivity.this)).put("battery", ((android.os.PowerManager) getSystemService(POWER_SERVICE)).isIgnoringBatteryOptimizations(getPackageName())).toString();
            } catch (Exception e) { return "{}"; }
        }
        @JavascriptInterface public void setWake(final boolean on) {
            runOnUiThread(() -> {
                prefs.edit().putBoolean("wake", on).apply();
                if (on) { askedOverlay = false; ensureWake(); } else stopService(new Intent(MainActivity.this, WakeService.class));
            });
        }
        @JavascriptInterface public boolean getFlagD(String n, boolean d) { return prefs.getBoolean(n, d); }
        @JavascriptInterface public void setPhoneControl(final boolean on) {
            runOnUiThread(() -> {
                prefs.edit().putBoolean("phonectl", on).apply();
                if (on) { ensureLink(); requestPermissions(PHONE_PERMS, REQ_PHONE); }
                else stopService(new Intent(MainActivity.this, LinkService.class));
            });
        }
        @JavascriptInterface public void askPhonePermissions() { runOnUiThread(() -> requestPermissions(PHONE_PERMS, REQ_PHONE)); }
        @JavascriptInterface public String phoneStatus() {
            try {
                return new JSONObject().put("on", prefs.getBoolean("phonectl", false)).put("linked", PhoneLink.running)
                        .put("contacts", checkSelfPermission(Manifest.permission.READ_CONTACTS) == PackageManager.PERMISSION_GRANTED).put("sms", checkSelfPermission(Manifest.permission.SEND_SMS) == PackageManager.PERMISSION_GRANTED)
                        .put("readsms", checkSelfPermission(Manifest.permission.READ_SMS) == PackageManager.PERMISSION_GRANTED).put("call", checkSelfPermission(Manifest.permission.CALL_PHONE) == PackageManager.PERMISSION_GRANTED)
                        .put("overlay", Settings.canDrawOverlays(MainActivity.this)).put("notif_access", RosieNotificationListener.enabled(MainActivity.this)).put("screen_control", RosieAccessibilityService.enabled(MainActivity.this)).toString();
            } catch (Exception e) { return "{}"; }
        }
        @JavascriptInterface public void openNotificationAccess() { runOnUiThread(() -> { try { startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)); } catch (Exception ignored) { } }); }
        @JavascriptInterface public void openScreenControl() { runOnUiThread(() -> { try { startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)); } catch (Exception ignored) { } }); }
        @JavascriptInterface public boolean appVisible() { return visible; }
        @JavascriptInterface public void log(String m) { android.util.Log.i("Rosie", "js: " + m); }
        @JavascriptInterface public void selfTestStart() { runOnUiThread(() -> SelfTest.start(MainActivity.this, web, MainActivity.this, false)); }
        @JavascriptInterface public void selfTestQuick() { runOnUiThread(() -> SelfTest.start(MainActivity.this, web, MainActivity.this, true)); }
        @JavascriptInterface public void fix(final String code) { runOnUiThread(() -> doFix(code)); }
        @JavascriptInterface public void askBatteryUnrestricted() {
            runOnUiThread(() -> {
                try { startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName()))); }
                catch (Exception e) { try { startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)); } catch (Exception ignored) { } }
            });
        }
        @JavascriptInterface public int appVersionCode() { try { return (int) getPackageManager().getPackageInfo(getPackageName(), 0).getLongVersionCode(); } catch (Exception e) { return 0; } }
        @JavascriptInterface public String appVersionName() { try { return getPackageManager().getPackageInfo(getPackageName(), 0).versionName; } catch (Exception e) { return "?"; } }
        @JavascriptInterface public int getIntPref(String k, int def) { return prefs.getInt(k, def); }
        @JavascriptInterface public void setIntPref(String k, int v) { prefs.edit().putInt(k, v).apply(); }
        @JavascriptInterface public String getPref(String k, String def) { return prefs.getString(k, def); }
        @JavascriptInterface public void setPref(String k, String v) { prefs.edit().putString(k, v).apply(); }
        @JavascriptInterface public void previewSound(final String kind, final String style) { new Thread(() -> Sounds.play(MainActivity.this, kind, style)).start(); }
        @JavascriptInterface public boolean getFlag(String n) { return prefs.getBoolean(n, false); }
        @JavascriptInterface public void setFlag(final String n, final boolean v) {
            runOnUiThread(() -> {
                prefs.edit().putBoolean(n, v).apply();
                if (n.equals("btmic") && v && Build.VERSION.SDK_INT >= 31 && checkSelfPermission("android.permission.BLUETOOTH_CONNECT") != PackageManager.PERMISSION_GRANTED)
                    requestPermissions(new String[]{"android.permission.BLUETOOTH_CONNECT"}, REQ_WAKE);
            });
        }
        @JavascriptInterface public String micInfo() { try { return MicRoute.describe(MainActivity.this, prefs.getBoolean("btmic", false)); } catch (Exception e) { return "phone microphone"; } }
        @JavascriptInterface public boolean isAssistant() {
            try { android.app.role.RoleManager rm = (android.app.role.RoleManager) getSystemService(ROLE_SERVICE); return rm.isRoleHeld(android.app.role.RoleManager.ROLE_ASSISTANT); } catch (Exception e) { return false; }
        }
        @JavascriptInterface public void setAssistant() { runOnUiThread(() -> openAssistantSettings()); }
        @JavascriptInterface public void trainStart() {
            runOnUiThread(() -> {
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) { requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC); return; }
                WakeTrainer.start(MainActivity.this, web, MainActivity.this);
            });
        }
        @JavascriptInterface public void trainReset() { WakeTrainer.reset(MainActivity.this); }
        @JavascriptInterface public String trainInfo() {
            return "{\"trained\":" + prefs.getInt("trained", 0) + ",\"aliases\":" + JSONObject.quote(prefs.getString("aliases", "")) + ",\"gate\":" + prefs.getFloat("gate", 0.012f) + "}";
        }
        @JavascriptInterface public void retry() { runOnUiThread(() -> web.loadUrl(baseUrl())); }
        @JavascriptInterface public void changeAddress() { runOnUiThread(MainActivity.this::askAddress); }
    }

    void ensureLink() {
        if (!prefs.getBoolean("phonectl", false)) return;
        if (!PhoneLink.running) startForegroundService(new Intent(this, LinkService.class));
    }

    // board #685 quirk-assistant-role-by-settings
    void openAssistantSettings() {
        Toast.makeText(this, "Tap \"Digital assistant app\" and choose Rosie.", Toast.LENGTH_LONG).show();
        try { startActivity(new Intent(Settings.ACTION_VOICE_INPUT_SETTINGS)); }
        catch (Exception e) { try { startActivity(new Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS)); } catch (Exception ignored) { } }
    }

    void doFix(String code) {
        try {
            switch (code) {
                case "perm_mic": requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC + 20); break;
                case "perm_notif": if (Build.VERSION.SDK_INT >= 33) requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, REQ_MIC + 21); break;
                case "perm_phone": requestPermissions(PHONE_PERMS, REQ_PHONE); break;
                case "battery": startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName()))); break;
                case "overlay": startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:" + getPackageName()))); break;
                case "notif_access": startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)); break;
                case "accessibility": startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)); break;
                case "assistant": openAssistantSettings(); break;
                case "phonectl": prefs.edit().putBoolean("phonectl", true).apply(); ensureLink(); requestPermissions(PHONE_PERMS, REQ_PHONE); break;
                case "wake": prefs.edit().putBoolean("wake", true).apply(); askedOverlay = false; ensureWake(); break;
                default: break;
            }
        } catch (Exception e) { Toast.makeText(this, "Could not open that screen: " + e.getMessage(), Toast.LENGTH_LONG).show(); }
    }

    void ensureWake() {
        if (!prefs.getBoolean("wake", false)) return;
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) { requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_WAKE); return; }
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != PackageManager.PERMISSION_GRANTED) { requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, REQ_WAKE); return; }
        if (!Settings.canDrawOverlays(this) && !askedOverlay) {
            askedOverlay = true;
            Toast.makeText(this, "Turn on \"Allow display over other apps\" for Rosie so she can open when you say Hey Rosie.", Toast.LENGTH_LONG).show();
            try { startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:" + getPackageName()))); } catch (Exception ignored) { }
            return;
        }
        if (!WakeService.running) startForegroundService(new Intent(this, WakeService.class));
    }

    @Override public void onRequestPermissionsResult(int code, String[] perms, int[] res) {
        web.evaluateJavascript("setTimeout(function(){ window.onAppResumeWizard && window.onAppResumeWizard(); }, 400)", null);
        if (code == REQ_PHONE) { web.evaluateJavascript("window.onAppResume && window.onAppResume()", null); return; }
        if (code == REQ_WAKE) {
            if (res.length == 0 || res[0] != PackageManager.PERMISSION_GRANTED) {
                prefs.edit().putBoolean("wake", false).apply();
                Toast.makeText(this, "Hey Rosie needs that permission, so it is off. You can turn it on again in Settings.", Toast.LENGTH_LONG).show();
                ensureLink(); return;
            }
            ensureWake(); ensureLink(); return;
        }
        if (code == REQ_MIC && pendingMic != null) {
            if (res.length > 0 && res[0] == PackageManager.PERMISSION_GRANTED) pendingMic.grant(pendingMic.getResources()); else pendingMic.deny();
            pendingMic = null;
        }
    }

    @Override protected void onActivityResult(int code, int result, Intent data) {
        if (code == 77) { web.evaluateJavascript("window.onAppResume && window.onAppResume()", null); return; }
        if (code == REQ_FILE && fileCallback != null) {
            fileCallback.onReceiveValue(android.webkit.WebChromeClient.FileChooserParams.parseResult(result, data));
            fileCallback = null;
        }
    }

    @Override public void onBackPressed() { if (web.canGoBack()) web.goBack(); else super.onBackPressed(); }
    @Override protected void onSaveInstanceState(Bundle b) { super.onSaveInstanceState(b); web.saveState(b); }
    @Override protected void onResume() {
        super.onResume(); visible = true; web.onResume();
        ensureWake();
        ensureLink();
        web.evaluateJavascript("window.onAppResume && window.onAppResume(); setTimeout(function(){ window.onAppResumeWizard && window.onAppResumeWizard(); }, 600)", null);
    }
    @Override protected void onPause() { visible = false; CookieManager.getInstance().flush(); web.onPause(); super.onPause(); }
}
