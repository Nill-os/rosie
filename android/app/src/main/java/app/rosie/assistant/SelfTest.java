package app.rosie.assistant;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.media.AudioRecord;
import android.os.Build;
import android.os.PowerManager;
import android.provider.Settings;
import android.util.Log;
import android.webkit.CookieManager;
import android.webkit.WebView;

import org.json.JSONArray;
import org.json.JSONObject;
import org.vosk.Model;
import org.vosk.Recognizer;

import java.net.HttpURLConnection;
import java.net.URL;

public class SelfTest {
    interface Step { String run() throws Exception; }

    static void start(final Context c, final WebView web, final android.app.Activity act, final boolean quick) {
        new Thread(() -> {
            JSONArray out = new JSONArray();
            step(out, c, "Phone and app", "", () -> "Android " + Build.VERSION.RELEASE + " (" + Build.MODEL + "), Rosie " + version(c));
            if (!quick) step(out, c, "Reaches your PC and is logged in", "", () -> {
                VoiceSession vs = new VoiceSession(c, new RosieOverlay(c));
                String cookie = CookieManager.getInstance().getCookie(vs.base());
                if (cookie == null || !cookie.contains("assistant_session")) throw new Exception("not logged in: open Rosie and enter the access code");
                HttpURLConnection h = (HttpURLConnection) new URL(vs.base() + "/api/phone/online").openConnection();
                h.setConnectTimeout(8000); h.setReadTimeout(8000); h.setRequestProperty("Cookie", cookie);
                if (h.getResponseCode() != 200) throw new Exception("PC answered " + h.getResponseCode() + " (is Tailscale on?)");
                return vs.base().replace("https://", "");
            });
            step(out, c, "Phone link to your PC is running", "phonectl", () -> {
                if (!c.getSharedPreferences("assistant", Context.MODE_PRIVATE).getBoolean("phonectl", false)) throw new Exception("Phone control is off: Settings > Phone control > Let Rosie use my phone");
                if (!PhoneLink.running) throw new Exception("not running yet: open the app once and wait a few seconds");
                return "connected";
            });
            step(out, c, "Microphone works", "perm_mic", () -> {
                if (c.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) throw new Exception("no microphone permission");
                MicRoute.acquire(c, false);
                AudioRecord r = MicRoute.record(c, 16000);
                try {
                    if (r.getState() != AudioRecord.STATE_INITIALIZED) throw new Exception("the microphone would not open");
                    short[] b = new short[3200]; int peak = 0, n = 0;
                    r.startRecording();
                    for (int i = 0; i < 6; i++) { n = r.read(b, 0, b.length); for (int k = 0; k < n; k++) peak = Math.max(peak, Math.abs(b[k])); }
                    if (peak == 0) throw new Exception("the microphone returns only silence (Android may be blocking it)");
                    return MicRoute.describe(c, false) + ", signal ok";
                } finally { try { r.stop(); } catch (Exception ignored) { } r.release(); MicRoute.release(c); }
            });
            step(out, c, "Offline 'Hey Rosie' model loads", "", () -> {
                long t0 = System.currentTimeMillis();
                Model m = new Model(WakeService.modelDir(c).getAbsolutePath());
                Recognizer rec = new Recognizer(m, 16000f);
                rec.acceptWaveForm(new short[1600], 1600);
                rec.close(); m.close();
                return "loaded in " + (System.currentTimeMillis() - t0) + " ms";
            });
            step(out, c, "'Hey Rosie' listener", "wake", () -> {
                if (!c.getSharedPreferences("assistant", Context.MODE_PRIVATE).getBoolean("wake", false)) throw new Exception("off: Settings > Hey Rosie > Turn on");
                if (!WakeService.running) throw new Exception("turned on but not running: open the app once");
                int tr = c.getSharedPreferences("assistant", Context.MODE_PRIVATE).getInt("trained", 0);
                return "listening; voice training " + (tr > 0 ? "done (" + tr + " of 6 clear)" : "not done yet");
            });
            step(out, c, "Battery is not limiting Rosie", "battery", () -> {
                PowerManager pm = (PowerManager) c.getSystemService(Context.POWER_SERVICE);
                if (!pm.isIgnoringBatteryOptimizations(c.getPackageName())) throw new Exception("Android may stop Rosie in the background: Settings > Apps > Rosie > Battery > Unrestricted");
                return "unrestricted";
            });
            step(out, c, "Popup over other apps allowed", "overlay", () -> { if (!Settings.canDrawOverlays(c)) throw new Exception("optional: allow 'Display over other apps' for the octopus popup"); return "allowed"; });
            step(out, c, "Notifications allowed", "perm_notif", () -> { if (Build.VERSION.SDK_INT >= 33 && c.checkSelfPermission("android.permission.POST_NOTIFICATIONS") != PackageManager.PERMISSION_GRANTED) throw new Exception("not allowed: Android Settings > Apps > Rosie > Notifications"); return "allowed"; });
            step(out, c, "Contacts, texts and calls", "perm_phone", () -> {
                String miss = "";
                if (c.checkSelfPermission(Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED) miss += " contacts";
                if (c.checkSelfPermission(Manifest.permission.SEND_SMS) != PackageManager.PERMISSION_GRANTED) miss += " texts";
                if (c.checkSelfPermission(Manifest.permission.READ_SMS) != PackageManager.PERMISSION_GRANTED) miss += " read-texts";
                if (c.checkSelfPermission(Manifest.permission.CALL_PHONE) != PackageManager.PERMISSION_GRANTED) miss += " calls";
                if (!miss.isEmpty()) throw new Exception("missing:" + miss + " (Settings > Phone control > Allow; Android may need 'Allow restricted settings' first)");
                return "all allowed";
            });
            step(out, c, "Notification access", "notif_access", () -> { if (!RosieNotificationListener.enabled(c)) throw new Exception("off: Settings > Phone control > Allow notification access"); if (RosieNotificationListener.instance == null) throw new Exception("allowed but not connected yet (toggle it off and on in Android settings)"); return "reading notifications"; });
            step(out, c, "Screen control", "accessibility", () -> { if (!RosieAccessibilityService.enabled(c)) throw new Exception("off: Settings > Phone control > Turn on screen control"); if (RosieAccessibilityService.instance == null) throw new Exception("enabled but not connected (toggle it off and on in Android Accessibility settings)"); return "ready"; });
            step(out, c, "Headphone button opens Rosie", "assistant", () -> {
                android.app.role.RoleManager rm = (android.app.role.RoleManager) c.getSystemService(Context.ROLE_SERVICE);
                if (!rm.isRoleHeld(android.app.role.RoleManager.ROLE_ASSISTANT)) throw new Exception("optional: Settings > Phone control > Use my headphone button for Rosie");
                return "Rosie is the assistant";
            });
            step(out, c, "Pocket sensor present", "", () -> {
                android.hardware.SensorManager sm = (android.hardware.SensorManager) c.getSystemService(Context.SENSOR_SERVICE);
                if (sm.getDefaultSensor(android.hardware.Sensor.TYPE_PROXIMITY) == null) throw new Exception("no proximity sensor: the pocket rule cannot work on this phone");
                return "ok";
            });
            if (!quick) step(out, c, "PC speech out (text to speech)", "", () -> {
                VoiceSession vs = new VoiceSession(c, new RosieOverlay(c));
                byte[] a = vs.http("/api/speak", "application/json", new JSONObject().put("text", "Okay.").toString().getBytes("UTF-8"), 60000);
                if (a.length < 2000) throw new Exception("the PC returned too little audio");
                return a.length / 1024 + " KB of audio received (not played)";
            });
            if (!quick) step(out, c, "PC speech in (transcribe)", "", () -> {
                VoiceSession vs = new VoiceSession(c, new RosieOverlay(c));
                vs.transcribe(new short[16000]);
                return "round trip ok";
            });
            if (!quick) step(out, c, "Full voice request", "", () -> {
                VoiceSession vs = new VoiceSession(c, new RosieOverlay(c));
                String r = vs.voice("what time is it", true);
                if (!r.matches(".*\\d.*")) throw new Exception("unexpected answer: " + r);
                return "answered: " + r;
            });
            final String json = out.toString();
            act.runOnUiThread(() -> web.evaluateJavascript("window.onSelfTest && window.onSelfTest(" + json + ", " + quick + ")", null));
        }, "selftest").start();
    }

    static void step(JSONArray out, Context c, String name, String fix, Step s) {
        JSONObject o = new JSONObject();
        try {
            String detail = s.run();
            o.put("name", name).put("ok", true).put("detail", detail);
            Log.i("Rosie", "selftest PASS " + name + " | " + detail);
        } catch (Throwable t) {
            try { o.put("name", name).put("ok", false).put("detail", String.valueOf(t.getMessage())).put("fix", fix); } catch (Exception ignored) { }
            Log.i("Rosie", "selftest FAIL " + name + " | " + t.getMessage());
        }
        out.put(o);
    }

    static String version(Context c) {
        try { return c.getPackageManager().getPackageInfo(c.getPackageName(), 0).versionName; } catch (Exception e) { return "?"; }
    }
}
