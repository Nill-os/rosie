package app.rosie.assistant;

import android.Manifest;
import android.app.SearchManager;
import android.content.ContentResolver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.database.Cursor;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.media.AudioManager;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.PowerManager;
import android.provider.AlarmClock;
import android.provider.ContactsContract;
import android.telephony.SmsManager;
import android.view.KeyEvent;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

// board #687 safety-confirm-before-send
public class PhoneActions {
    static boolean has(Context c, String perm) { return c.checkSelfPermission(perm) == PackageManager.PERMISSION_GRANTED; }

    static JSONObject run(Context c, String action, JSONObject a) throws Exception {
        switch (action) {
            case "status": return status(c);
            case "find_contact": return findContact(c, a.optString("name"));
            case "keep_note": return keepNote(c, a.optString("title"), a.optString("text"));
            case "sms": return sms(c, a.optString("to"), a.optString("text"));
            case "call": return call(c, a.optString("to"));
            case "read_sms": return readSms(c, a.optInt("count", 5), a.optString("from"));
            case "alarm": return alarm(c, a.optString("time"), a.optString("label"));
            case "timer": return timer(c, a.optDouble("minutes", 1), a.optString("label"));
            case "open_app": return openApp(c, a.optString("name"));
            case "music": return music(c, a.optString("action"));
            case "torch": return torch(c, a.optBoolean("on", true));
            case "volume": return volume(c, a.optString("direction"), a.has("percent") && !a.isNull("percent") ? a.optInt("percent") : -1);
            case "navigate": return view(c, Uri.parse("google.navigation:q=" + Uri.encode(a.optString("place"))), "Started navigation to " + a.optString("place"));
            case "search": return search(c, a.optString("query"));
            case "notifications": return RosieNotificationListener.list(c, a.optInt("count", 8), a.optString("app"));
            case "notif_reply": return RosieNotificationListener.reply(c, a.optString("id"), a.optString("text"));
            case "screen_read": case "screen_tap": case "screen_type": case "screen_scroll": case "screen_press": return screen(action, a);
            default: return new JSONObject().put("error", "I do not know how to do '" + action + "' on the phone yet.");
        }
    }

    static JSONObject screen(String action, JSONObject a) throws Exception {
        RosieAccessibilityService s = RosieAccessibilityService.instance;
        if (s == null) return new JSONObject().put("error", "Screen control is not turned on. On the phone: Rosie > Settings > Phone control > Turn on screen control (then Android Settings > Accessibility > Rosie).");
        switch (action) {
            case "screen_read": return s.read();
            case "screen_tap": return s.tap(a.optString("target"), a.optBoolean("force", false));
            case "screen_type": return s.type(a.optString("text"), a.optString("target"), a.optBoolean("append", false));
            case "screen_scroll": return s.scroll(a.optString("direction", "down"));
            default: return s.press(a.optString("key"));
        }
    }

    static JSONObject start(Context c, Intent i, String done) throws Exception {
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        c.startActivity(i);
        return new JSONObject().put("done", true).put("note", done);
    }

    static JSONObject view(Context c, Uri u, String done) throws Exception { return start(c, new Intent(Intent.ACTION_VIEW, u), done); }

    static JSONObject status(Context c) throws Exception {
        Intent b = c.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        int level = b == null ? -1 : b.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) * 100 / Math.max(1, b.getIntExtra(BatteryManager.EXTRA_SCALE, 100));
        boolean charging = b != null && b.getIntExtra(BatteryManager.EXTRA_STATUS, -1) == BatteryManager.BATTERY_STATUS_CHARGING;
        PowerManager pm = (PowerManager) c.getSystemService(Context.POWER_SERVICE);
        boolean headset = false;
        for (android.media.AudioDeviceInfo d : ((AudioManager) c.getSystemService(Context.AUDIO_SERVICE)).getDevices(AudioManager.GET_DEVICES_OUTPUTS)) {
            int t = d.getType();
            if (t == android.media.AudioDeviceInfo.TYPE_BLUETOOTH_A2DP || t == android.media.AudioDeviceInfo.TYPE_BLE_HEADSET || t == android.media.AudioDeviceInfo.TYPE_WIRED_HEADSET || t == android.media.AudioDeviceInfo.TYPE_WIRED_HEADPHONES || t == android.media.AudioDeviceInfo.TYPE_USB_HEADSET) headset = true;
        }
        return new JSONObject().put("connected", true).put("headset", headset).put("battery_percent", level).put("charging", charging).put("screen_on", pm.isInteractive())
                .put("can_text", has(c, Manifest.permission.SEND_SMS)).put("can_read_texts", has(c, Manifest.permission.READ_SMS)).put("can_see_contacts", has(c, Manifest.permission.READ_CONTACTS))
                .put("can_call", has(c, Manifest.permission.CALL_PHONE));
    }

    static JSONObject findContact(Context c, String name) throws Exception {
        if (!has(c, Manifest.permission.READ_CONTACTS)) return new JSONObject().put("error", "Rosie does not have permission to read contacts yet. On the phone: Rosie > Settings > Phone control > Allow.");
        JSONArray out = new JSONArray();
        Set<String> seen = new LinkedHashSet<>();
        try (Cursor cur = c.getContentResolver().query(ContactsContract.CommonDataKinds.Phone.CONTENT_URI, new String[]{ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER, ContactsContract.CommonDataKinds.Phone.TYPE},
                ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME + " LIKE ?", new String[]{"%" + name + "%"}, ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME + " LIMIT 8")) {
            while (cur != null && cur.moveToNext()) {
                String num = cur.getString(1) == null ? "" : cur.getString(1).replaceAll("[^0-9+]", "");
                if (num.isEmpty() || !seen.add(num)) continue;
                int t = cur.getInt(2);
                out.put(new JSONObject().put("name", cur.getString(0)).put("number", num).put("type", t == ContactsContract.CommonDataKinds.Phone.TYPE_MOBILE ? "mobile" : t == ContactsContract.CommonDataKinds.Phone.TYPE_HOME ? "home" : t == ContactsContract.CommonDataKinds.Phone.TYPE_WORK ? "work" : "other"));
            }
        }
        return new JSONObject().put("matches", out);
    }

    static JSONObject keepNote(Context c, String title, String text) throws Exception {
        Intent i = new Intent("com.google.android.gms.actions.CREATE_NOTE").setPackage("com.google.android.keep").putExtra(Intent.EXTRA_SUBJECT, title).putExtra(Intent.EXTRA_TEXT, text);
        if (i.resolveActivity(c.getPackageManager()) == null) {
            i = new Intent(Intent.ACTION_SEND).setType("text/plain").setPackage("com.google.android.keep").putExtra(Intent.EXTRA_SUBJECT, title).putExtra(Intent.EXTRA_TEXT, text);
            if (i.resolveActivity(c.getPackageManager()) == null) return new JSONObject().put("error", "Google Keep is not installed on the phone.");
        }
        return start(c, i, "Sent the note to Google Keep (it may show Keep on screen for a moment).");
    }

    static JSONObject sms(Context c, String to, String text) throws Exception {
        if (!has(c, Manifest.permission.SEND_SMS)) return new JSONObject().put("error", "Rosie does not have permission to send texts yet. On the phone: Rosie > Settings > Phone control > Allow.");
        SmsManager sm = android.os.Build.VERSION.SDK_INT >= 31 ? c.getSystemService(SmsManager.class) : SmsManager.getDefault();
        ArrayList<String> parts = sm.divideMessage(text);
        sm.sendMultipartTextMessage(to, null, parts, null, null);
        return new JSONObject().put("done", true).put("note", "Text sent to " + to + " (" + parts.size() + " part" + (parts.size() > 1 ? "s" : "") + ").");
    }

    static JSONObject call(Context c, String to) throws Exception {
        Uri u = Uri.parse("tel:" + Uri.encode(to));
        if (has(c, Manifest.permission.CALL_PHONE)) return start(c, new Intent(Intent.ACTION_CALL, u), "Calling " + to + ".");
        start(c, new Intent(Intent.ACTION_DIAL, u), "");
        return new JSONObject().put("done", true).put("note", "Opened the dialer with " + to + "; tap the green button (Rosie has no permission to place calls yet).");
    }

    static JSONObject readSms(Context c, int count, String from) throws Exception {
        if (!has(c, Manifest.permission.READ_SMS)) return new JSONObject().put("error", "Rosie does not have permission to read texts yet. On the phone: Rosie > Settings > Phone control > Allow.");
        String sel = null; String[] args = null;
        if (!from.isEmpty()) {
            String digits = from.replaceAll("[^0-9]", "");
            if (digits.isEmpty()) {
                JSONObject m = findContact(c, from);
                JSONArray arr = m.optJSONArray("matches");
                if (arr != null && arr.length() > 0) digits = arr.getJSONObject(0).optString("number").replaceAll("[^0-9]", "");
            }
            if (!digits.isEmpty()) { String tail = digits.length() > 7 ? digits.substring(digits.length() - 7) : digits; sel = "address LIKE ?"; args = new String[]{"%" + tail}; }
        }
        JSONArray out = new JSONArray();
        try (Cursor cur = c.getContentResolver().query(Uri.parse("content://sms/inbox"), new String[]{"address", "body", "date"}, sel, args, "date DESC LIMIT " + Math.max(1, Math.min(count, 15)))) {
            while (cur != null && cur.moveToNext()) {
                String body = cur.getString(1) == null ? "" : cur.getString(1);
                out.put(new JSONObject().put("from", cur.getString(0)).put("text", body.length() > 300 ? body.substring(0, 300) + "…" : body).put("when", new java.text.SimpleDateFormat("EEE MMM d h:mm a").format(new java.util.Date(cur.getLong(2)))));
            }
        }
        return new JSONObject().put("messages", out);
    }

    static JSONObject alarm(Context c, String time, String label) throws Exception {
        String[] p = time.split(":");
        Intent i = new Intent(AlarmClock.ACTION_SET_ALARM).putExtra(AlarmClock.EXTRA_HOUR, Integer.parseInt(p[0].trim())).putExtra(AlarmClock.EXTRA_MINUTES, Integer.parseInt(p[1].trim()))
                .putExtra(AlarmClock.EXTRA_MESSAGE, label).putExtra(AlarmClock.EXTRA_SKIP_UI, true);
        return start(c, i, "Alarm set for " + time + ".");
    }

    static JSONObject timer(Context c, double minutes, String label) throws Exception {
        Intent i = new Intent(AlarmClock.ACTION_SET_TIMER).putExtra(AlarmClock.EXTRA_LENGTH, (int) Math.round(minutes * 60)).putExtra(AlarmClock.EXTRA_MESSAGE, label).putExtra(AlarmClock.EXTRA_SKIP_UI, true);
        return start(c, i, "Timer started for " + minutes + " minutes.");
    }

    static JSONObject openApp(Context c, String name) throws Exception {
        PackageManager pm = c.getPackageManager();
        List<ResolveInfo> apps = pm.queryIntentActivities(new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0);
        String q = name.toLowerCase().trim();
        ResolveInfo best = null; int bestScore = 0;
        for (ResolveInfo r : apps) {
            String l = r.loadLabel(pm).toString().toLowerCase();
            int score = l.equals(q) ? 100 : l.startsWith(q) ? 80 : l.contains(q) ? 60 : q.contains(l) && l.length() > 3 ? 40 : 0;
            if (score > bestScore) { bestScore = score; best = r; }
        }
        if (best == null) return new JSONObject().put("error", "I could not find an app called '" + name + "' on the phone.");
        Intent i = pm.getLaunchIntentForPackage(best.activityInfo.packageName);
        return start(c, i, "Opened " + best.loadLabel(pm) + ".");
    }

    static JSONObject music(Context c, String action) throws Exception {
        int key;
        switch (action.toLowerCase()) {
            case "play": key = KeyEvent.KEYCODE_MEDIA_PLAY; break;
            case "pause": case "stop": key = KeyEvent.KEYCODE_MEDIA_PAUSE; break;
            case "next": case "skip": key = KeyEvent.KEYCODE_MEDIA_NEXT; break;
            case "previous": case "back": key = KeyEvent.KEYCODE_MEDIA_PREVIOUS; break;
            default: return new JSONObject().put("error", "Use play, pause, next or previous.");
        }
        AudioManager am = (AudioManager) c.getSystemService(Context.AUDIO_SERVICE);
        am.dispatchMediaKeyEvent(new KeyEvent(KeyEvent.ACTION_DOWN, key));
        am.dispatchMediaKeyEvent(new KeyEvent(KeyEvent.ACTION_UP, key));
        return new JSONObject().put("done", true).put("note", "Sent '" + action + "' to the music player.");
    }

    static JSONObject torch(Context c, boolean on) throws Exception {
        CameraManager cm = (CameraManager) c.getSystemService(Context.CAMERA_SERVICE);
        for (String id : cm.getCameraIdList()) {
            Boolean flash = cm.getCameraCharacteristics(id).get(CameraCharacteristics.FLASH_INFO_AVAILABLE);
            if (flash != null && flash) { cm.setTorchMode(id, on); return new JSONObject().put("done", true).put("note", "Flashlight " + (on ? "on" : "off") + "."); }
        }
        return new JSONObject().put("error", "No flashlight found.");
    }

    static JSONObject volume(Context c, String dir, int percent) throws Exception {
        AudioManager am = (AudioManager) c.getSystemService(Context.AUDIO_SERVICE);
        int max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
        if (percent >= 0) am.setStreamVolume(AudioManager.STREAM_MUSIC, Math.max(0, Math.min(max, max * percent / 100)), AudioManager.FLAG_SHOW_UI);
        else if (dir.equals("up")) am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_RAISE, AudioManager.FLAG_SHOW_UI);
        else if (dir.equals("down")) am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_LOWER, AudioManager.FLAG_SHOW_UI);
        else if (dir.equals("mute")) am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_MUTE, AudioManager.FLAG_SHOW_UI);
        else return new JSONObject().put("error", "Say up, down, mute, or a percent.");
        return new JSONObject().put("done", true).put("volume_percent", am.getStreamVolume(AudioManager.STREAM_MUSIC) * 100 / max);
    }

    static JSONObject search(Context c, String q) throws Exception {
        Intent i = new Intent(Intent.ACTION_WEB_SEARCH).putExtra(SearchManager.QUERY, q);
        if (i.resolveActivity(c.getPackageManager()) == null) return view(c, Uri.parse("https://www.google.com/search?q=" + Uri.encode(q)), "Searching for " + q);
        return start(c, i, "Searching for " + q);
    }
}
