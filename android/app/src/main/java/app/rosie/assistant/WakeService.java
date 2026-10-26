package app.rosie.assistant;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.content.res.AssetManager;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.media.ToneGenerator;
import android.media.AudioManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.provider.Settings;
import android.util.Log;

import org.json.JSONObject;
import org.vosk.Model;
import org.vosk.Recognizer;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.regex.Pattern;

// board #693 safety-wake-audio-discarded
public class WakeService extends Service {
    static volatile boolean running = false;
    static final String CHANNEL = "wake", STOP = "app.rosie.assistant.STOP_WAKE", CONVERSE = "app.rosie.assistant.CONVERSE", CANCEL = "app.rosie.assistant.CANCEL_CONVERSATION", LINK = "app.rosie.assistant.LINK";
    static volatile boolean near = false;
    private android.hardware.SensorManager sensors;
    private android.hardware.SensorEventListener proxListener;

    static volatile VoiceSession activeSession;
    static volatile boolean sessionActive = false;
    static final String GRAMMAR = "[\"hey rosie\", \"hay rosie\", \"hi rosie\", \"hey rosy\", \"hey rose e\", \"[unk]\"]";
    static final Pattern WAKE = Pattern.compile("\\b(hey|hay|hi)\\s+(rosie|rosy|rose e)\\b");

    static final String[] DEFAULT_ALIASES = {"hey rosie", "hay rosie", "hi rosie", "hey rosy", "hey rosey", "hey rosi", "a rosie"};

    static File modelDir(Context c) throws Exception {
        File dir = new File(c.getFilesDir(), "model-v1");
        if (!new File(dir, "ok").exists()) {
            File tmp = new File(c.getFilesDir(), "model-tmp");
            copyAssets(c.getAssets(), "model-en-us", tmp);
            new File(tmp, "ok").createNewFile();
            tmp.renameTo(dir);
        }
        return dir;
    }

    static java.util.Set<String> aliases(Context c) {
        java.util.Set<String> a = new java.util.LinkedHashSet<>(java.util.Arrays.asList(DEFAULT_ALIASES));
        String learned = c.getSharedPreferences("assistant", MODE_PRIVATE).getString("aliases", "");
        for (String x : learned.split("\\|")) if (!x.trim().isEmpty()) a.add(x.trim());
        return a;
    }

    static float gate(Context c) {
        SharedPreferences p = c.getSharedPreferences("assistant", MODE_PRIVATE);
        float g = p.getFloat("gate", 0.012f);
        return p.getBoolean("pocket", false) ? Math.max(0.003f, g * 0.35f) : g;
    }

    static boolean isWake(String text, java.util.Set<String> aliases) { return isWake(text, aliases, false); }

    static boolean isWake(String text, java.util.Set<String> aliases, boolean exactOnly) {
        text = text.trim().toLowerCase();
        for (String a : aliases) {
            if (text.equals(a)) return true;
            if (!exactOnly && text.startsWith(a + " ") && text.split("\\s+").length <= a.split("\\s+").length + 1) return true;
        }
        return false;
    }

    private volatile boolean stop = false;
    private Thread loop;
    private PowerManager.WakeLock lock;

    @Override public IBinder onBind(Intent i) { return null; }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && STOP.equals(intent.getAction())) {
            VoiceSession live = activeSession;
            if (live != null) live.cancel();
            getSharedPreferences("assistant", MODE_PRIVATE).edit().putBoolean("wake", false).apply();
            stopSelf();
            return START_NOT_STICKY;
        }
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Hey Rosie", NotificationManager.IMPORTANCE_LOW));
        NotificationManager nm2 = nm;
        nm2.createNotificationChannel(new NotificationChannel("wake_heard", "Rosie heard you", NotificationManager.IMPORTANCE_HIGH));
        PendingIntent stopPi = PendingIntent.getService(this, 1, new Intent(this, WakeService.class).setAction(STOP), PendingIntent.FLAG_IMMUTABLE);
        PendingIntent open = PendingIntent.getActivity(this, 2, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        Notification n = new Notification.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.ic_btn_speak_now).setContentTitle("Rosie is listening for \"Hey Rosie\"")
                .setContentText("Heard on this phone only. Use the button to turn it off.").setContentIntent(open).setOngoing(true)
                .addAction(new Notification.Action.Builder(null, "Turn off Hey Rosie", stopPi).build()).build();
        try {
            if (Build.VERSION.SDK_INT >= 29) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE); else startForeground(1, n);
        } catch (Throwable t) {  // board #693 why-stop-not-restart-loop
            Log.e("Rosie", "cannot start the microphone service: " + t);
            stopSelf();
            return START_NOT_STICKY;
        }
        if (intent != null && CANCEL.equals(intent.getAction())) {
            VoiceSession s = activeSession;
            if (s != null) s.cancel();
            return START_STICKY;
        }
        boolean converse = intent != null && CONVERSE.equals(intent.getAction());
        boolean wakeOn = getSharedPreferences("assistant", MODE_PRIVATE).getBoolean("wake", false);
        if (converse && !sessionActive) {
            sessionActive = true;
            new Thread(() -> {
                try { woke(); } finally {
                    sessionActive = false;
                    SharedPreferences sp2 = getSharedPreferences("assistant", MODE_PRIVATE);
                    if (!sp2.getBoolean("wake", false)) stopSelf();
                }
            }, "converse").start();
        }
        if (loop == null && wakeOn) {
            if (sensors == null) {
                sensors = (android.hardware.SensorManager) getSystemService(SENSOR_SERVICE);
                android.hardware.Sensor prox = sensors.getDefaultSensor(android.hardware.Sensor.TYPE_PROXIMITY);
                if (prox != null) {
                    final float far = prox.getMaximumRange();
                    proxListener = new android.hardware.SensorEventListener() {
                        @Override public void onSensorChanged(android.hardware.SensorEvent e) { near = e.values[0] < Math.min(far, 5f); }
                        @Override public void onAccuracyChanged(android.hardware.Sensor s, int a) { }
                    };
                    sensors.registerListener(proxListener, prox, android.hardware.SensorManager.SENSOR_DELAY_NORMAL);
                }
            }
            running = true;
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            lock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "rosie:wake");
            lock.acquire();
            loop = new Thread(this::listen, "wake");
            loop.start();
            new Thread(() -> Sounds.play(this, "on")).start();
            new android.os.Handler(android.os.Looper.getMainLooper()).post(() -> android.widget.Toast.makeText(this, "Hey Rosie is on. Say \"Hey Rosie\" any time.", android.widget.Toast.LENGTH_LONG).show());
        }
        return converse && !wakeOn ? START_NOT_STICKY : START_STICKY;
    }

    static void copyAssets(AssetManager am, String from, File to) throws Exception {
        String[] kids = am.list(from);
        if (kids == null || kids.length == 0) {
            try (InputStream in = am.open(from); OutputStream out = new FileOutputStream(to)) {
                byte[] b = new byte[65536]; int n;
                while ((n = in.read(b)) > 0) out.write(b, 0, n);
            }
            return;
        }
        to.mkdirs();
        for (String k : kids) copyAssets(am, from + "/" + k, new File(to, k));
    }

    private void listen() {
        Model model = null; Recognizer rec = null; AudioRecord mic = null;
        try {
            model = new Model(modelDir(this).getAbsolutePath());
            rec = new Recognizer(model, 16000f);
            double peak = 0;
            short[] buf = new short[3200];
            long coolUntil = 0;
            String curRoute = "";
            String lastState = "";
            long beat = 0; int finals = 0; double beatPeak = 0;
            int silentFrames = 0;
            boolean warned = false;
            while (!stop) {
                boolean idle = MainActivity.visible || sessionActive || System.currentTimeMillis() < coolUntil;
                SharedPreferences sp = getSharedPreferences("assistant", MODE_PRIVATE);
                boolean pocketRule = sp.getBoolean("pocketrule", true);
                boolean pocketNow = near && !((PowerManager) getSystemService(POWER_SERVICE)).isInteractive();
                boolean headset = MicRoute.wired(this) != null || MicRoute.bluetooth(this) != null;
                if (pocketRule && pocketNow && !headset) idle = true;
                boolean bt = sp.getBoolean("btmic", false) || (pocketRule && pocketNow);
                String route = MicRoute.key(this, bt);
                if (mic != null && !route.equals(curRoute)) idle = true;
                boolean dbg = sp.getBoolean("wake_debug", false);
                if (dbg) {
                    String st = idle ? (MainActivity.visible ? "paused: app on screen" : sessionActive ? "paused: conversation" : System.currentTimeMillis() < coolUntil ? "paused: cooldown" : (pocketRule && pocketNow && !headset) ? "paused: pocket, no headset" : "paused: route change") : "listening via " + route;
                    if (!st.equals(lastState)) { Log.i("Rosie", "wake: " + st); lastState = st; }
                }
                if (idle) {
                    if (mic != null) { try { mic.stop(); } catch (Exception ignored) { } mic.release(); mic = null; MicRoute.release(this); }
                    Thread.sleep(300);
                    continue;
                }
                if (mic == null) {
                    MicRoute.acquire(this, bt);
                    curRoute = route;
                    mic = MicRoute.record(this, 16000);
                    if (mic.getState() != AudioRecord.STATE_INITIALIZED) { mic.release(); mic = null; MicRoute.release(this); Thread.sleep(2000); continue; }
                    mic.startRecording();
                    rec.reset();
                    silentFrames = 0;
                }
                int n = mic.read(buf, 0, buf.length);
                if (n <= 0) { Thread.sleep(50); continue; }
                int maxAbs = 0; for (int i = 0; i < n; i++) maxAbs = Math.max(maxAbs, Math.abs(buf[i]));
                if (maxAbs == 0) { if (++silentFrames > 60 && !warned) { warned = true; micBlocked(); } } else { silentFrames = 0; warned = false; }  // board #693 quirk-background-mic-silence
                double sum = 0; for (int i = 0; i < n; i++) sum += (buf[i] / 32768.0) * (buf[i] / 32768.0);
                peak = Math.max(peak, Math.sqrt(sum / n));
                beatPeak = Math.max(beatPeak, Math.sqrt(sum / n));
                if (sp.getBoolean("wake_debug", false) && System.currentTimeMillis() - beat > 5000) { Log.i("Rosie", "wake: alive, mic level peak=" + String.format("%.4f", beatPeak) + " phrases=" + finals); beat = System.currentTimeMillis(); beatPeak = 0; }
                if (!rec.acceptWaveForm(buf, n)) continue;
                String text = new JSONObject(rec.getResult()).optString("text", "");
                double loud = peak; peak = 0;
                if (sp.getBoolean("wake_debug", false)) { finals++; Log.i("Rosie", "wake: heard [" + text + "] level=" + String.format("%.4f", loud) + " gate=" + String.format("%.4f", gate(this))); }
                if (text.isEmpty()) continue;
                boolean heardName = text.contains("ros") || text.contains("hey");
                boolean pocket = getSharedPreferences("assistant", MODE_PRIVATE).getBoolean("pocket", false);
                boolean match = isWake(text, aliases(this), pocket);
                if (match && loud < gate(this)) { Log.i("Rosie", "ignored (too quiet " + loud + "): " + text); continue; }
                if (heardName) Log.i("Rosie", (match ? "WAKE: " : "ignored: ") + text);
                if (match) {
                    rec.reset();
                    if (mic != null) { try { mic.stop(); } catch (Exception ignored) { } mic.release(); mic = null; MicRoute.release(this); }
                    sessionActive = true;  // board #693 why-no-second-session
                    try { woke(); } finally { sessionActive = false; }
                    coolUntil = System.currentTimeMillis() + 1500;
                }
            }
        } catch (Throwable t) {
            android.util.Log.e("Rosie", "wake listener stopped: " + t);
        } finally {
            if (mic != null) { try { mic.stop(); } catch (Exception ignored) { } mic.release(); MicRoute.release(this); }
            if (rec != null) rec.close();
            if (model != null) model.close();
            running = false;
            loop = null;
            if (lock != null && lock.isHeld()) lock.release();
        }
    }

    private void micBlocked() {
        PendingIntent open = PendingIntent.getActivity(this, 4, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        Notification n = new Notification.Builder(this, "wake_heard").setSmallIcon(android.R.drawable.ic_btn_speak_now).setContentTitle("Hey Rosie can't hear the microphone")
                .setContentText("Android paused it. Tap to open Rosie and it will start listening again.").setContentIntent(open).setAutoCancel(true).build();
        ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).notify(3, n);
    }

    private void woke() {
        if (Sounds.vibrate(this)) try { ((android.os.Vibrator) getSystemService(Context.VIBRATOR_SERVICE)).vibrate(android.os.VibrationEffect.createOneShot(70, android.os.VibrationEffect.DEFAULT_AMPLITUDE)); } catch (Exception ignored) { }
        Sounds.play(this, "wake");
        RosieOverlay ov = new RosieOverlay(this);
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        if (!(Settings.canDrawOverlays(this) && pm.isInteractive())) ov = new RosieOverlay(this) { @Override void show(String st, String cap) { } };
        new VoiceSession(this, ov).run();
    }

    @Override public void onDestroy() {
        stop = true;
        if (sensors != null && proxListener != null) sensors.unregisterListener(proxListener);
        super.onDestroy();
    }
}
