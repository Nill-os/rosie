package app.rosie.assistant;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaPlayer;
import android.media.MediaRecorder;
import android.util.Log;
import android.webkit.CookieManager;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.UUID;

/** One hands-free conversation after "Hey Rosie": listen, send to your PC, speak the answer, listen again, until you stop talking or say stop.
 *  The microphone audio goes only to your own assistant over your Tailscale link. */
public class VoiceSession {
    private final Context ctx;
    private final RosieOverlay ov;
    volatile boolean stop = false;
    private MediaPlayer player;
    private volatile HttpURLConnection cur;
    static final String STATUS_CHANNEL = "rosie_status";

    /** Status notification (shows in the shade, pops up briefly) so you can tell what she is doing even with the phone in a pocket. */
    void status(String title, String text) {
        try {
            android.app.NotificationManager nm = (android.app.NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
            android.app.NotificationChannel ch = new android.app.NotificationChannel(STATUS_CHANNEL, "Rosie status", android.app.NotificationManager.IMPORTANCE_HIGH);
            ch.setSound(null, null); ch.enableVibration(false); ch.setShowBadge(false);
            nm.createNotificationChannel(ch);
            android.app.PendingIntent cancel = android.app.PendingIntent.getService(ctx, 9, new android.content.Intent(ctx, WakeService.class).setAction(WakeService.CANCEL), android.app.PendingIntent.FLAG_IMMUTABLE);
            android.app.Notification n = new android.app.Notification.Builder(ctx, STATUS_CHANNEL).setSmallIcon(android.R.drawable.ic_btn_speak_now).setContentTitle(title).setContentText(text)
                    .setOnlyAlertOnce(true).setOngoing(true).setCategory(android.app.Notification.CATEGORY_STATUS)
                    .addAction(new android.app.Notification.Action.Builder(null, "Stop", cancel).build()).build();
            nm.notify(5, n);
        } catch (Throwable ignored) { }
    }

    void clearStatus() {
        try { ((android.app.NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE)).cancel(5); } catch (Throwable ignored) { }
    }

    /** A short blip plus a tiny buzz: "I stopped listening, I'm working on it". */
    void thinkingCue() {
        new Thread(() -> Sounds.play(ctx, "thinking")).start();
        if (Sounds.vibrate(ctx)) try { ((android.os.Vibrator) ctx.getSystemService(Context.VIBRATOR_SERVICE)).vibrate(android.os.VibrationEffect.createOneShot(40, android.os.VibrationEffect.DEFAULT_AMPLITUDE)); } catch (Exception ignored) { }
    }

    VoiceSession(Context c, RosieOverlay o) { ctx = c; ov = o; }

    String base() { return ctx.getSharedPreferences("assistant", Context.MODE_PRIVATE).getString("url", MainActivity.DEFAULT_URL); }

    /** A tap on the popup, Stop on the notification, or the headphone button: stop everything right now (microphone, waiting for the PC, her voice) and close the popup. */
    void cancel() {
        stop = true;
        ov.hide();
        try { if (player != null) player.stop(); } catch (Exception ignored) { }
        final HttpURLConnection c = cur;
        if (c != null) new Thread(() -> { try { c.disconnect(); } catch (Exception ignored) { } }).start();      // give up on a request still waiting for the PC
        clearStatus();
    }

    /** kind: "wake", "thinking", "done", "on", "error" (see Sounds). */
    static void chime(Context c, boolean rising) { Sounds.play(c, rising ? "wake" : "done"); }

    /** Runs the whole conversation. Returns false if it could not even start (not logged in / PC unreachable). */
    void run() {
        stop = false;
        final android.media.AudioManager audio = (android.media.AudioManager) ctx.getSystemService(Context.AUDIO_SERVICE);
        final android.media.AudioFocusRequest focus = new android.media.AudioFocusRequest.Builder(android.media.AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                .setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANT).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()).build();
        audio.requestAudioFocus(focus);                          // pauses your music while she talks; it resumes afterwards
        MicRoute.acquire(ctx, true);                             // use the headset microphone when you are wearing one
        android.media.session.MediaSession media = null;
        try {                                                    // pressing the headphone button while she talks stops her at once
            media = new android.media.session.MediaSession(ctx, "Rosie");
            media.setCallback(new android.media.session.MediaSession.Callback() {
                @Override public boolean onMediaButtonEvent(android.content.Intent i) { cancel(); return true; }
            });
            media.setPlaybackState(new android.media.session.PlaybackState.Builder().setActions(android.media.session.PlaybackState.ACTION_PLAY_PAUSE | android.media.session.PlaybackState.ACTION_STOP)
                    .setState(android.media.session.PlaybackState.STATE_PLAYING, 0, 1f).build());
            media.setActive(true);
        } catch (Exception ignored) { }
        ov.onTap = this::cancel;
        WakeService.activeSession = this;
        ov.show("listening", "");
        status("Rosie is listening", "Go ahead and talk.");
        int empties = 0;
        String carry = null;
        boolean first = true;
        try {
            while (!stop) {
                short[] pcm = null; String text = null;
                if (carry != null) { text = carry; carry = null; }
                else {
                    ov.set("listening", "");
                    status("Rosie is listening", first ? "Go ahead and talk." : "Your turn.");
                    pcm = listen(first ? 8000 : 10000);
                    Log.i("Rosie", "listen -> " + (pcm == null ? "nothing heard" : pcm.length / 16 + " ms") + " stop=" + stop);
                    if (pcm == null || stop) break;
                    ov.set("thinking", "");
                    thinkingCue();
                    status("Rosie stopped listening and is thinking…", "Working on what you said.");
                    text = transcribe(pcm);
                    Log.i("Rosie", "heard: " + text);
                    if (text.isEmpty()) { if (++empties >= 2) break; continue; }
                }
                empties = 0;
                ov.set("thinking", text);
                status("Rosie is thinking…", "You said: " + text);
                String low = text.toLowerCase().replaceAll("[^a-z ]", "").trim();
                if (low.matches("(stop|goodbye|bye|that is all|thats all|thanks that is all|thank you that is all|never mind|nevermind)( now| please| rosie)?")) { speak("Okay."); break; }
                String reply = voice(text);
                if (stop) break;
                speak(reply);
                first = false;
                JSONObject auto = lastVoice.optJSONObject("auto");
                if (auto != null && !stop) { carry = autoWindow(auto, reply); }
            }
        } catch (Exception e) {
            if (stop) { Log.i("Rosie", "cancelled by tap"); return; }
            Log.e("Rosie", "conversation error: " + e);
            ov.set("thinking", e.getMessage() == null ? "Can't reach Rosie. Is Tailscale on?" : e.getMessage());
            try { Thread.sleep(3500); } catch (InterruptedException ignored) { }
        } finally {
            ov.hide();
            clearStatus();
            WakeService.activeSession = null;
            MicRoute.release(ctx);
            if (media != null) { try { media.setActive(false); media.release(); } catch (Exception ignored) { } }
            chime(ctx, false);
            audio.abandonAudioFocusRequest(focus);
        }
    }

    /** How long a pause means "I'm done" (Settings: quick / normal / patient). */
    int endpointMs() { return ctx.getSharedPreferences("assistant", Context.MODE_PRIVATE).getInt("endpoint_ms", 1200); }

    /** Records one spoken request. Returns null if nobody spoke. Stops 1.4 s after you finish. */
    short[] listen(int noSpeechMs) throws Exception {
        int rate = 16000, frame = 1600;
        AudioRecord mic = MicRoute.record(ctx, rate);
        if (mic.getState() != AudioRecord.STATE_INITIALIZED) { mic.release(); throw new Exception("Microphone busy"); }
        ArrayList<short[]> frames = new ArrayList<>();
        short[] buf = new short[frame];
        int errs = 0;
        try {
            mic.startRecording();
            long t0 = System.currentTimeMillis(), last = 0;
            boolean spoke = false;
            double amb = 0, peak = 0; int ambN = 0;
            while (!stop) {
                int n = mic.read(buf, 0, frame);
                if (n < 0) { if (++errs > 5) throw new Exception("Microphone error"); Thread.sleep(50); continue; }     // the audio route changed or another app took the mic
                if (n == 0) { if (System.currentTimeMillis() - t0 > 40000) break; continue; }
                errs = 0;
                double sum = 0; for (int i = 0; i < n; i++) sum += (buf[i] / 32768.0) * (buf[i] / 32768.0);
                double v = Math.sqrt(sum / n);
                ov.level = (float) Math.min(1.0, v * 9);
                long now = System.currentTimeMillis();
                short[] copy = new short[n]; System.arraycopy(buf, 0, copy, 0, n); frames.add(copy);
                if (now - t0 < 500) { amb += v; ambN++; continue; }
                double ambient = ambN > 0 ? amb / ambN : 0, thr = Math.max(0.010, 2.5 * ambient);
                if (v > peak) peak = v;
                if (v > thr) { spoke = true; last = now; }
                if (spoke && now - last > endpointMs()) break;
                if (!spoke && now - t0 > noSpeechMs) { Log.i("Rosie", "no speech: ambient=" + ambient + " peak=" + peak + " threshold=" + thr); return null; }
                if (now - t0 > 40000) break;
            }
        } finally { try { mic.stop(); } catch (Exception ignored) { } mic.release(); ov.level = 0f; }
        if (stop) return null;
        int total = 0; for (short[] f : frames) total += f.length;
        short[] all = new short[total]; int o = 0; for (short[] f : frames) { System.arraycopy(f, 0, all, o, f.length); o += f.length; }
        return all;
    }

    static byte[] wav(short[] pcm, int rate) {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        int dl = pcm.length * 2;
        byte[] h = new byte[44];
        put(h, 0, "RIFF"); le(h, 4, 36 + dl); put(h, 8, "WAVE"); put(h, 12, "fmt "); le(h, 16, 16); h[20] = 1; h[22] = 1; le(h, 24, rate); le(h, 28, rate * 2); h[32] = 2; h[34] = 16; put(h, 36, "data"); le(h, 40, dl);
        b.write(h, 0, 44);
        for (short s : pcm) { b.write(s & 0xff); b.write((s >> 8) & 0xff); }
        return b.toByteArray();
    }
    static void put(byte[] a, int o, String s) { for (int i = 0; i < s.length(); i++) a[o + i] = (byte) s.charAt(i); }
    static void le(byte[] a, int o, int v) { a[o] = (byte) v; a[o + 1] = (byte) (v >> 8); a[o + 2] = (byte) (v >> 16); a[o + 3] = (byte) (v >> 24); }

    byte[] http(String path, String contentType, byte[] body, int readMs) throws Exception {
        String cookie = CookieManager.getInstance().getCookie(base());
        if (cookie == null || !cookie.contains("assistant_session")) throw new Exception("Open the Rosie app once to log in.");
        HttpURLConnection c = (HttpURLConnection) new URL(base() + path).openConnection();
        cur = c;
        c.setConnectTimeout(15000); c.setReadTimeout(readMs); c.setRequestMethod("POST"); c.setDoOutput(true);
        c.setRequestProperty("Cookie", cookie); c.setRequestProperty("Content-Type", contentType);
        try (OutputStream os = c.getOutputStream()) { os.write(body); }
        int code = c.getResponseCode();
        if (code == 401) throw new Exception("Open the Rosie app once to log in again.");
        if (code >= 400) throw new Exception("Rosie's PC answered with an error (" + code + ").");
        try (InputStream in = c.getInputStream()) {
            ByteArrayOutputStream out = new ByteArrayOutputStream(); byte[] b = new byte[16384]; int n;
            while ((n = in.read(b)) > 0) out.write(b, 0, n);
            return out.toByteArray();
        } finally { cur = null; }
    }

    String transcribe(short[] pcm) throws Exception {
        String bnd = "----rosie" + UUID.randomUUID().toString().replace("-", "");
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        b.write(("--" + bnd + "\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"a.wav\"\r\nContent-Type: audio/wav\r\n\r\n").getBytes());
        b.write(wav(pcm, 16000));
        b.write(("\r\n--" + bnd + "--\r\n").getBytes());
        return new JSONObject(new String(http("/api/transcribe", "multipart/form-data; boundary=" + bnd, b.toByteArray(), 120000))).optString("text", "").trim();
    }

    JSONObject lastVoice = new JSONObject();

    String voice(String text) throws Exception { return voice(text, false); }

    String voice(String text, boolean ephemeral) throws Exception {
        JSONObject j = new JSONObject().put("text", text).put("selection", "").put("ephemeral", ephemeral);
        lastVoice = new JSONObject(new String(http("/api/voice", "application/json", j.toString().getBytes("UTF-8"), 240000), "UTF-8"));
        String r = lastVoice.optString("reply", "");
        return r.isEmpty() ? "I have nothing to say to that." : r;
    }

    /** Siri/Gemini style: say what is about to happen, give a short window to say "cancel", then do it. Returns text to treat as the next request, or null. */
    String autoWindow(JSONObject auto, String spokenSummary) throws Exception {
        String token = auto.getString("token");
        status("Doing it in 3 seconds", "Say cancel or tap Stop to stop.");
        ov.set("listening", "Say cancel to stop");
        short[] w = listen(auto.optInt("window_ms", 3000));
        if (stop) { http("/api/cancel", "application/json", new JSONObject().put("token", token).toString().getBytes("UTF-8"), 15000); return null; }
        if (w == null) {                                                          // nothing said: go ahead
            JSONObject r = new JSONObject(new String(http("/api/confirm", "application/json", new JSONObject().put("token", token).toString().getBytes("UTF-8"), 60000), "UTF-8"));
            if (!"done".equals(r.optString("status"))) speak("That did not work: " + r.optString("message", "unknown problem"));
            else if (spokenSummary.startsWith("Text")) speak("Sent.");
            return null;
        }
        http("/api/cancel", "application/json", new JSONObject().put("token", token).toString().getBytes("UTF-8"), 15000);   // you said something: hold it
        String t = transcribe(w);
        String low = t.toLowerCase().replaceAll("[^a-z ]", "").trim();
        if (low.matches("(no|nope|cancel|cancel it|stop|stop that|wait|hold on|dont|dont send|dont call|never mind|nevermind)( please)?")) { speak("Okay, cancelled."); return null; }
        return t.isEmpty() ? null : t;                                           // anything else is your next request
    }

    static String spoken(String t) {
        t = t.replaceAll("https?://\\S+", "a link").replaceAll("[*_`#>|]+", "").replaceAll("\\n\\s*[-•]\\s*", ". ").replaceAll("\\s+", " ").trim();
        if (t.length() > 700) { String cut = t.substring(0, 700); int i = Math.max(cut.lastIndexOf(". "), 300); t = cut.substring(0, i + 1) + " The rest is in your voice chat."; }
        return t;
    }

    /** Speaks the answer sentence by sentence: the first sentence starts playing while the rest is still being made, so you hear her much sooner. */
    void speak(String reply) throws Exception {
        if (stop) return;
        ov.set("speaking", reply.length() > 160 ? reply.substring(0, 157) + "…" : reply);
        status("Rosie is speaking", reply.length() > 120 ? reply.substring(0, 117) + "…" : reply);
        final java.util.List<String> parts = chunks(spoken(reply));
        final java.util.concurrent.BlockingQueue<Object> q = new java.util.concurrent.LinkedBlockingQueue<>();
        Thread fetch = new Thread(() -> {
            try {
                for (String p : parts) {
                    if (stop) break;
                    q.put(http("/api/speak", "application/json", new JSONObject().put("text", p).toString().getBytes("UTF-8"), 120000));
                }
                q.put("end");
            } catch (Exception e) { try { q.put(stop ? "end" : e); } catch (InterruptedException ignored) { } }
        }, "tts-fetch");
        fetch.start();
        while (!stop) {
            Object o = q.take();
            if (o instanceof Exception) throw (Exception) o;
            if (o instanceof String) break;
            playBytes((byte[]) o);
        }
    }

    /** Splits text into sentence-sized pieces (small ones joined), at most 6, so speech can start early without sounding choppy. */
    static java.util.List<String> chunks(String t) {
        java.util.List<String> out = new java.util.ArrayList<>();
        StringBuilder cur = new StringBuilder();
        for (String sent : t.split("(?<=[.!?])\\s+")) {
            if (sent.isEmpty()) continue;
            if (cur.length() > 0) cur.append(' ');
            cur.append(sent);
            if (cur.length() >= 45 && out.size() < 5) { out.add(cur.toString()); cur.setLength(0); }
        }
        if (cur.length() > 0) out.add(cur.toString());
        return out;
    }

    void playBytes(byte[] audio) throws Exception {
        if (stop) return;
        File f = new File(ctx.getCacheDir(), "say.wav");
        try (FileOutputStream o = new FileOutputStream(f)) { o.write(audio); }
        final Object done = new Object();
        player = new MediaPlayer();
        player.setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANT).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build());
        player.setDataSource(f.getAbsolutePath());
        player.setOnCompletionListener(m -> { synchronized (done) { done.notifyAll(); } });
        player.prepare();
        if (stop) { player.release(); player = null; return; }
        player.start();
        synchronized (done) {
            while (player.isPlaying() && !stop) { ov.level = 0.45f + 0.3f * (float) Math.sin(System.currentTimeMillis() / 120.0); done.wait(80); }
        }
        ov.level = 0f;
        try { player.release(); } catch (Exception ignored) { }
        player = null;
    }
}
