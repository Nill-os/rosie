package app.rosie.assistant;

import android.content.Context;
import android.util.Log;
import android.webkit.CookieManager;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/** Keeps a connection to your PC and does the jobs Rosie sends (see PhoneActions). Runs only while "Let Rosie use my phone" is on. */
public class PhoneLink implements Runnable {
    static volatile boolean running = false;
    private final Context ctx;
    private volatile boolean stop = false;

    PhoneLink(Context c) { ctx = c; }

    void stop() { stop = true; running = false; }

    String base() { return ctx.getSharedPreferences("assistant", Context.MODE_PRIVATE).getString("url", MainActivity.DEFAULT_URL); }

    byte[] request(String method, String path, byte[] body, int readMs) throws Exception {
        String cookie = CookieManager.getInstance().getCookie(base());
        if (cookie == null || !cookie.contains("assistant_session")) throw new Exception("not logged in");
        HttpURLConnection c = (HttpURLConnection) new URL(base() + path).openConnection();
        c.setConnectTimeout(15000); c.setReadTimeout(readMs); c.setRequestMethod(method);
        c.setRequestProperty("Cookie", cookie);
        if (body != null) { c.setDoOutput(true); c.setRequestProperty("Content-Type", "application/json"); try (OutputStream os = c.getOutputStream()) { os.write(body); } }
        if (c.getResponseCode() >= 400) throw new Exception("HTTP " + c.getResponseCode());
        try (InputStream in = c.getInputStream()) {
            ByteArrayOutputStream out = new ByteArrayOutputStream(); byte[] b = new byte[8192]; int n;
            while ((n = in.read(b)) > 0) out.write(b, 0, n);
            return out.toByteArray();
        }
    }

    @Override public void run() {
        running = true;
        int fails = 0;
        while (!stop) {
            try {
                JSONObject j = new JSONObject(new String(request("GET", "/api/phone/poll?wait=25", null, 40000), "UTF-8"));
                fails = 0;
                JSONObject cmd = j.optJSONObject("cmd");
                if (cmd == null) continue;
                if (stop || !ctx.getSharedPreferences("assistant", Context.MODE_PRIVATE).getBoolean("phonectl", false)) break;     // switched off while waiting: do NOT run it
                JSONObject res = new JSONObject().put("id", cmd.getString("id"));
                try {
                    Log.i("Rosie", "phone job: " + cmd.getString("action"));
                    JSONObject out = PhoneActions.run(ctx, cmd.getString("action"), cmd.optJSONObject("args") == null ? new JSONObject() : cmd.getJSONObject("args"));
                    if (out.has("error")) res.put("ok", false).put("data", out.getString("error")); else res.put("ok", true).put("data", out);
                } catch (Throwable t) {
                    res.put("ok", false).put("data", t.getClass().getSimpleName() + ": " + t.getMessage());
                }
                request("POST", "/api/phone/result", res.toString().getBytes("UTF-8"), 15000);
            } catch (Exception e) {
                fails++;
                try { Thread.sleep(Math.min(30000, 3000L * fails)); } catch (InterruptedException ignored) { }
            }
        }
        running = false;
    }
}
