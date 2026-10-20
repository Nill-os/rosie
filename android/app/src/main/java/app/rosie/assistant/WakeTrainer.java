package app.rosie.assistant;

import android.content.Context;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.webkit.WebView;

import org.json.JSONArray;
import org.json.JSONObject;
import org.vosk.Model;
import org.vosk.Recognizer;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/** "Train my voice": you say "Hey Rosie" six times. It learns how the recogniser hears YOUR voice (accent, speed) and how loudly you speak,
 *  and then only wakes for phrases that match. Everything stays on the phone. */
public class WakeTrainer {
    static volatile boolean running = false;
    static final int ROUNDS = 6;

    static void start(final Context ctx, final WebView web, final android.app.Activity act) {
        if (running) return;
        running = true;
        new Thread(() -> {
            Model model = null;
            try {
                model = new Model(WakeService.modelDir(ctx).getAbsolutePath());
                Map<String, Integer> heard = new LinkedHashMap<>();
                ArrayList<Double> peaks = new ArrayList<>();
                Set<String> known = WakeService.aliases(ctx);
                int ok = 0;
                for (int i = 1; i <= ROUNDS; i++) {
                    push(act, web, new JSONObject().put("step", i).put("of", ROUNDS).put("state", "ready"));
                    Thread.sleep(1200);
                    Sounds.play(ctx, "wake");
                    android.util.Log.i("Rosie", "train listening " + i);
                    push(act, web, new JSONObject().put("step", i).put("of", ROUNDS).put("state", "listening"));
                    double[] peak = new double[1];
                    short[] pcm = capture(ctx, peak);
                    String text = "";
                    if (pcm != null) {
                        Recognizer r = new Recognizer(model, 16000f);
                        r.acceptWaveForm(pcm, pcm.length);
                        text = new JSONObject(r.getFinalResult()).optString("text", "").trim();
                        r.close();
                    }
                    boolean good = !text.isEmpty() && WakeService.isWake(text, known);
                    if (!text.isEmpty() && text.split("\\s+").length <= 3) heard.put(text, heard.containsKey(text) ? heard.get(text) + 1 : 1);
                    if (pcm != null) peaks.add(peak[0]);
                    if (good) ok++;
                    android.util.Log.i("Rosie", "train heard [" + text + "] good=" + good);
                    push(act, web, new JSONObject().put("step", i).put("of", ROUNDS).put("state", "heard").put("text", text.isEmpty() ? "(nothing heard)" : text).put("good", good));
                    Thread.sleep(700);
                }
                // learn: any short phrase heard at least twice becomes a way your voice says it; the loudness gate follows your quietest good attempt
                StringBuilder learned = new StringBuilder();
                for (Map.Entry<String, Integer> e : heard.entrySet()) if (e.getValue() >= 2 && e.getKey().contains("r")) learned.append(e.getKey()).append("|");
                double minPeak = 1; for (double p : peaks) minPeak = Math.min(minPeak, p);
                float gate = peaks.isEmpty() ? 0.012f : (float) Math.max(0.006, Math.min(0.06, minPeak * 0.5));
                if (ok >= 3 || learned.length() > 0) {
                    String prev = ctx.getSharedPreferences("assistant", Context.MODE_PRIVATE).getString("aliases", "");
                    ctx.getSharedPreferences("assistant", Context.MODE_PRIVATE).edit().putString("aliases", prev + learned).putFloat("gate", gate).putInt("trained", ok).apply();
                }
                push(act, web, new JSONObject().put("state", "done").put("good", ok).put("of", ROUNDS).put("learned", learned.toString()).put("gate", gate).put("saved", ok >= 3 || learned.length() > 0));
            } catch (Throwable t) {
                try { push(act, web, new JSONObject().put("state", "error").put("text", String.valueOf(t.getMessage()))); } catch (Exception ignored) { }
            } finally {
                if (model != null) model.close();
                running = false;
            }
        }, "train").start();
    }

    static void reset(Context ctx) { ctx.getSharedPreferences("assistant", Context.MODE_PRIVATE).edit().remove("aliases").remove("gate").remove("trained").apply(); }

    static void push(android.app.Activity act, final WebView web, JSONObject j) {
        final String js = "window.onTrain && window.onTrain(" + j + ")";
        act.runOnUiThread(() -> web.evaluateJavascript(js, null));
    }

    /** Records one attempt: waits up to 6 s for you to speak, ends 0.9 s after you stop. Returns null if nothing was said. */
    static short[] capture(Context ctx, double[] peakOut) {
        int rate = 16000, frame = 1600;
        MicRoute.acquire(ctx, true);                     // train on the same microphone you will really use (headset if you are wearing one)
        AudioRecord mic = MicRoute.record(ctx, rate);
        if (mic.getState() != AudioRecord.STATE_INITIALIZED) { mic.release(); MicRoute.release(ctx); return null; }
        ArrayList<short[]> frames = new ArrayList<>();
        short[] buf = new short[frame];
        long t0 = System.currentTimeMillis(), last = 0;
        boolean spoke = false;
        double amb = 0; int ambN = 0, total = 0;
        mic.startRecording();
        try {
            while (true) {
                int n = mic.read(buf, 0, frame);
                if (n <= 0) continue;
                double sum = 0; for (int i = 0; i < n; i++) sum += (buf[i] / 32768.0) * (buf[i] / 32768.0);
                double v = Math.sqrt(sum / n);
                long now = System.currentTimeMillis();
                short[] c = new short[n]; System.arraycopy(buf, 0, c, 0, n); frames.add(c); total += n;
                if (now - t0 < 400) { amb += v; ambN++; continue; }
                double thr = Math.max(0.008, 2.5 * (ambN > 0 ? amb / ambN : 0));
                if (v > thr) { spoke = true; last = now; if (v > peakOut[0]) peakOut[0] = v; }
                if (spoke && now - last > 900) break;
                if (!spoke && now - t0 > 6000) return null;
                if (now - t0 > 12000) break;
            }
        } finally { mic.stop(); mic.release(); MicRoute.release(ctx); }
        short[] all = new short[total]; int o = 0; for (short[] f : frames) { System.arraycopy(f, 0, all, o, f.length); o += f.length; }
        return all;
    }
}
