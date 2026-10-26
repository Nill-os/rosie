package app.rosie.assistant;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

public class RosieAccessibilityService extends AccessibilityService {
    static volatile RosieAccessibilityService instance;
    static volatile long haltedUntil = 0;
    static final Pattern BLOCKED = Pattern.compile("(bank|wallet|paypal|venmo|cashapp|chase|wellsfargo|capitalone|authenticator|password|1password|bitwarden|lastpass|keepass|dashlane|com\\.android\\.settings|packageinstaller|permissioncontroller|com\\.android\\.vending|securitycenter|\\.pay\\b|googlepay|samsungpay|coinbase|binance|crypto)", Pattern.CASE_INSENSITIVE);
    static final Pattern DANGER = Pattern.compile("\\b(send|pay|buy|purchase|order|checkout|place order|confirm|delete|remove|uninstall|transfer|submit|post|publish|share|call|sign out|log out|reset|erase|clear all|install|allow|grant|accept|agree|subscribe|book|reserve)\\b", Pattern.CASE_INSENSITIVE);
    private final List<AccessibilityNodeInfo> nodes = new ArrayList<>();
    private final Handler ui = new Handler(Looper.getMainLooper());

    @Override public void onServiceConnected() { instance = this; }
    @Override public void onAccessibilityEvent(AccessibilityEvent e) { }
    @Override public void onInterrupt() { }
    @Override public boolean onUnbind(Intent i) { instance = null; return super.onUnbind(i); }

    static boolean enabled(Context c) {
        String s = android.provider.Settings.Secure.getString(c.getContentResolver(), android.provider.Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
        return s != null && s.contains(c.getPackageName());
    }

    void indicator() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel("screen", "Screen control", NotificationManager.IMPORTANCE_HIGH));
        PendingIntent stop = PendingIntent.getBroadcast(this, 11, new Intent(this, HaltReceiver.class), PendingIntent.FLAG_IMMUTABLE);
        nm.notify(6, new Notification.Builder(this, "screen").setSmallIcon(android.R.drawable.ic_menu_view).setContentTitle("Rosie is controlling your screen").setContentText("Tap Stop to take over right now.")
                .setOngoing(true).setOnlyAlertOnce(true).addAction(new Notification.Action.Builder(null, "Stop", stop).build()).build());
        ui.removeCallbacksAndMessages("ind");
        ui.postAtTime(() -> nm.cancel(6), "ind", android.os.SystemClock.uptimeMillis() + 25000);
    }

    JSONObject guard() throws Exception {
        if (System.currentTimeMillis() < haltedUntil) return new JSONObject().put("error", "You pressed Stop, so Rosie is not touching the screen right now. Ask again to let her continue.");
        return null;
    }

    static String text(AccessibilityNodeInfo n) {
        CharSequence t = n.getText() != null ? n.getText() : n.getContentDescription();
        return t == null ? "" : t.toString();
    }

    JSONObject read() throws Exception {
        JSONObject g = guard(); if (g != null) return g;
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return new JSONObject().put("error", "I can't see the screen right now (it may be locked or off).");
        String pkg = String.valueOf(root.getPackageName());
        if (BLOCKED.matcher(pkg).find()) return new JSONObject().put("error", "That screen (" + pkg + ") is off limits for safety: banking, payments, passwords and security settings.");
        indicator();
        nodes.clear();
        JSONArray out = new JSONArray();
        walk(root, out, 0);
        return new JSONObject().put("app", AppLabel.of(this, pkg)).put("package", pkg).put("elements", out);
    }

    private void walk(AccessibilityNodeInfo n, JSONArray out, int depth) throws Exception {
        if (n == null || out.length() >= 90 || depth > 40) return;
        boolean interesting = !text(n).isEmpty() || n.isClickable() || n.isEditable() || n.isScrollable() || n.isCheckable();
        if (n.isVisibleToUser() && interesting) {
            JSONObject o = new JSONObject().put("i", nodes.size());
            String t = n.isPassword() ? "[password field]" : text(n);
            if (!t.isEmpty()) o.put("text", t.length() > 120 ? t.substring(0, 120) : t);
            if (n.isClickable()) o.put("tap", true);
            if (n.isEditable()) o.put("edit", true);
            if (n.isScrollable()) o.put("scroll", true);
            if (n.isChecked()) o.put("checked", true);
            nodes.add(n);
            out.put(o);
        }
        for (int k = 0; k < n.getChildCount(); k++) walk(n.getChild(k), out, depth + 1);
    }

    AccessibilityNodeInfo find(String target) {
        try { int i = Integer.parseInt(target.trim()); if (i >= 0 && i < nodes.size()) return nodes.get(i); } catch (NumberFormatException ignored) { }
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return null;
        List<AccessibilityNodeInfo> hits = root.findAccessibilityNodeInfosByText(target);
        return hits.isEmpty() ? null : hits.get(0);
    }

    JSONObject tap(String target, boolean force) throws Exception {
        JSONObject g = guard(); if (g != null) return g;
        AccessibilityNodeInfo n = find(target);
        if (n == null) return new JSONObject().put("error", "I could not find '" + target + "' on the screen. Read the screen again.");
        String pkg = String.valueOf(n.getPackageName());
        if (BLOCKED.matcher(pkg).find()) return new JSONObject().put("error", "That screen is off limits for safety.");
        String label = text(n);
        AccessibilityNodeInfo c = n;
        while (c != null && !c.isClickable()) c = c.getParent();
        if (label.isEmpty() && c != null) label = text(c);
        if (!force && DANGER.matcher(label).find()) return new JSONObject().put("needs_confirm", true).put("label", label).put("app", AppLabel.of(this, pkg));
        indicator();
        if (c != null && c.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return new JSONObject().put("done", true).put("note", "Tapped '" + label + "'.");
        Rect r = new Rect(); n.getBoundsInScreen(r);
        Path p = new Path(); p.moveTo(r.centerX(), r.centerY());
        dispatchGesture(new GestureDescription.Builder().addStroke(new GestureDescription.StrokeDescription(p, 0, 60)).build(), null, null);
        return new JSONObject().put("done", true).put("note", "Tapped '" + label + "' by position.");
    }

    JSONObject type(String text, String target, boolean append) throws Exception {
        JSONObject g = guard(); if (g != null) return g;
        AccessibilityNodeInfo n = target.isEmpty() ? getRootInActiveWindow() == null ? null : getRootInActiveWindow().findFocus(AccessibilityNodeInfo.FOCUS_INPUT) : find(target);
        if (n == null) for (AccessibilityNodeInfo x : nodes) if (x.isEditable()) { n = x; break; }
        if (n == null || !n.isEditable()) return new JSONObject().put("error", "No text box to type into. Tap the box first, then read the screen.");
        if (n.isPassword() || BLOCKED.matcher(String.valueOf(n.getPackageName())).find()) return new JSONObject().put("error", "I never type into password fields or protected screens.");
        indicator();
        String base = append && n.getText() != null && !n.getText().toString().equals(String.valueOf(n.getHintText())) ? n.getText().toString() : "";
        Bundle b = new Bundle();
        b.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, base + text);
        return n.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, b) ? new JSONObject().put("done", true).put("note", "Typed it.") : new JSONObject().put("error", "That box would not accept text.");
    }

    JSONObject scroll(String dir) throws Exception {
        JSONObject g = guard(); if (g != null) return g;
        for (AccessibilityNodeInfo n : nodes) if (n.isScrollable()) {
            indicator();
            boolean ok = n.performAction(dir.equals("up") ? AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD : AccessibilityNodeInfo.ACTION_SCROLL_FORWARD);
            return ok ? new JSONObject().put("done", true).put("note", "Scrolled " + dir + ".") : new JSONObject().put("error", "Could not scroll.");
        }
        return new JSONObject().put("error", "Nothing scrollable on this screen (read the screen first).");
    }

    JSONObject press(String key) throws Exception {
        JSONObject g = guard(); if (g != null) return g;
        int a;
        switch (key) {
            case "back": a = GLOBAL_ACTION_BACK; break;
            case "home": a = GLOBAL_ACTION_HOME; break;
            case "recents": a = GLOBAL_ACTION_RECENTS; break;
            case "notifications": a = GLOBAL_ACTION_NOTIFICATIONS; break;
            case "quick_settings": a = GLOBAL_ACTION_QUICK_SETTINGS; break;
            default: return new JSONObject().put("error", "Use back, home, recents, notifications or quick_settings.");
        }
        indicator();
        return performGlobalAction(a) ? new JSONObject().put("done", true).put("note", "Pressed " + key + ".") : new JSONObject().put("error", "Could not press " + key + ".");
    }

    static class AppLabel {
        static String of(Context c, String pkg) { return RosieNotificationListener.label(c, pkg); }
    }

    public static class HaltReceiver extends android.content.BroadcastReceiver {
        @Override public void onReceive(Context c, Intent i) {
            haltedUntil = System.currentTimeMillis() + 60000;
            ((NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE)).cancel(6);
        }
    }
}
