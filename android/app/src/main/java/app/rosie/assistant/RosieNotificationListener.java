package app.rosie.assistant;

import android.app.Notification;
import android.app.RemoteInput;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;

// board #689 safety-notifications-memory-only
public class RosieNotificationListener extends NotificationListenerService {
    static volatile RosieNotificationListener instance;

    @Override public void onListenerConnected() { instance = this; }
    @Override public void onListenerDisconnected() { instance = null; }

    static boolean enabled(Context c) {
        String s = android.provider.Settings.Secure.getString(c.getContentResolver(), "enabled_notification_listeners");
        return s != null && s.contains(c.getPackageName());
    }

    static String label(Context c, String pkg) {
        try { return c.getPackageManager().getApplicationLabel(c.getPackageManager().getApplicationInfo(pkg, 0)).toString(); } catch (Exception e) { return pkg; }
    }

    static Notification.Action replyAction(StatusBarNotification sbn) {
        Notification.Action[] acts = sbn.getNotification().actions;
        if (acts == null) return null;
        for (Notification.Action a : acts) if (a.getRemoteInputs() != null && a.getRemoteInputs().length > 0) return a;
        return null;
    }

    static JSONObject list(Context c, int count, String app) throws Exception {
        RosieNotificationListener l = instance;
        if (l == null) return new JSONObject().put("error", "Rosie does not have notification access yet. On the phone: Rosie > Settings > Phone control > Allow notification access.");
        JSONArray out = new JSONArray();
        StatusBarNotification[] all = l.getActiveNotifications();
        java.util.Arrays.sort(all, (a, b) -> Long.compare(b.getPostTime(), a.getPostTime()));
        for (StatusBarNotification s : all) {
            if (s.getPackageName().equals(c.getPackageName()) || s.isOngoing()) continue;
            String lab = label(c, s.getPackageName());
            if (!app.isEmpty() && !lab.toLowerCase().contains(app.toLowerCase())) continue;
            Bundle e = s.getNotification().extras;
            CharSequence title = e.getCharSequence(Notification.EXTRA_TITLE), text = e.getCharSequence(Notification.EXTRA_TEXT);
            out.put(new JSONObject().put("id", s.getKey()).put("app", lab).put("from", title == null ? "" : title.toString()).put("text", text == null ? "" : (text.length() > 300 ? text.subSequence(0, 300) + "…" : text.toString()))
                    .put("can_reply", replyAction(s) != null).put("when", new java.text.SimpleDateFormat("h:mm a").format(new java.util.Date(s.getPostTime()))));
            if (out.length() >= Math.max(1, Math.min(count, 20))) break;
        }
        return new JSONObject().put("notifications", out);
    }

    static JSONObject reply(Context c, String key, String text) throws Exception {
        RosieNotificationListener l = instance;
        if (l == null) return new JSONObject().put("error", "Rosie does not have notification access yet.");
        for (StatusBarNotification s : l.getActiveNotifications()) {
            if (!s.getKey().equals(key)) continue;
            Notification.Action a = replyAction(s);
            if (a == null) return new JSONObject().put("error", "That notification has no reply box.");
            android.app.RemoteInput[] ri = a.getRemoteInputs();
            Bundle b = new Bundle();
            for (android.app.RemoteInput r : ri) b.putCharSequence(r.getResultKey(), text);
            Intent i = new Intent();
            RemoteInput.addResultsToIntent(ri, i, b);
            a.actionIntent.send(c, 0, i);
            return new JSONObject().put("done", true).put("note", "Replied in " + label(c, s.getPackageName()) + ".");
        }
        return new JSONObject().put("error", "That notification is gone (it was dismissed or replaced). Ask for the notifications again.");
    }
}
