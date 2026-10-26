package app.rosie.assistant;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;

public class Sounds {
    static final int SR = 22050;

    static double hz(int semis) { return 261.63 * Math.pow(2, semis / 12.0); }

    static double pulse(double phase, double duty) { return (phase % 1.0) < duty ? 1.0 : -1.0; }

    static float[] notes(int[] semis, int ms, double duty, boolean soft) {
        int per = SR * ms / 1000;
        float[] out = new float[per * semis.length];
        double ph = 0;
        for (int k = 0; k < semis.length; k++) {
            double f = hz(semis[k]);
            for (int i = 0; i < per; i++) {
                ph += f / SR;
                double env = Math.min(1.0, Math.min(i / (SR * 0.003), (per - i) / (SR * 0.012)));
                double w = soft ? Math.sin(2 * Math.PI * ph) : pulse(ph, duty);
                out[k * per + i] = (float) (w * env * (soft ? 0.7 : 0.45));
            }
        }
        return out;
    }

    static float[] sweep(double f0, double f1, int ms, double duty) {
        int n = SR * ms / 1000; float[] out = new float[n]; double ph = 0;
        for (int i = 0; i < n; i++) {
            double f = f0 + (f1 - f0) * i / n; ph += f / SR;
            out[i] = (float) (pulse(ph, duty) * 0.45 * Math.min(1.0, Math.min(i / (SR * 0.004), (n - i) / (SR * 0.02))));
        }
        return out;
    }

    static float[] render(String style, String kind) {
        boolean soft = style.equals("soft");
        float[] w;
        switch (kind) {
            case "wake":     w = soft ? notes(new int[]{-5, 2}, 110, 0.5, true) : notes(new int[]{0, 4, 7, 12}, 38, 0.5, false); break;
            case "thinking": w = soft ? notes(new int[]{2, -5}, 60, 0.5, true) : notes(new int[]{7, 2, -2}, 32, 0.5, false); break;
            case "done":     w = soft ? notes(new int[]{2, -5}, 110, 0.5, true) : notes(new int[]{12, 7, 4, 0}, 38, 0.5, false); break;
            case "on":       w = soft ? notes(new int[]{-5, 2, 7}, 70, 0.5, true) : sweep(150, 520, 190, 0.5); break;
            default:         w = soft ? notes(new int[]{-12, -17}, 90, 0.5, true) : notes(new int[]{-10, -17, -22}, 60, 0.5, false);
        }
        float y = 0;  // board #691 why-soften-square-wave
        for (int i = 0; i < w.length; i++) { y += 0.28f * (w[i] - y); w[i] = Math.max(-1f, Math.min(1f, y * 1.5f)); }
        return w;
    }

    static float volume(Context c) {
        switch (c.getSharedPreferences("assistant", Context.MODE_PRIVATE).getString("sound_vol", "medium")) {
            case "low": return 0.3f;
            case "high": return 0.9f;
            default: return 0.6f;
        }
    }

    static String style(Context c) {
        String st = c.getSharedPreferences("assistant", Context.MODE_PRIVATE).getString("sound_style", "game");
        return st.equals("glitch") ? "game" : st;  // board #691 fix-glitch-style-removed
    }

    static void play(Context c, String kind) { play(c, kind, style(c)); }

    static void play(Context c, String kind, String style) {
        if (style.equals("off")) return;
        AudioTrack tr = null;
        try {
            float[] f = render(style, kind);
            boolean wireless = wirelessOutput(c);
            int lead = SR * (wireless ? 280 : 40) / 1000;  // board #691 quirk-bluetooth-wake-lead
            int tail = SR * (wireless ? 260 : 100) / 1000;  // board #691 quirk-headset-tail-delay
            short[] s = new short[lead + f.length + tail];
            float v = volume(c) * 32000f;
            int fade = Math.min(f.length / 4, SR / 100);
            for (int i = 0; i < f.length; i++) {
                float e = Math.min(1f, Math.min(i, f.length - 1 - i) / (float) Math.max(1, fade));
                s[lead + i] = (short) Math.max(-32768, Math.min(32767, f[i] * v * e));
            }
            tr = new AudioTrack.Builder().setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANT).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())  // board #691 why-same-volume-as-voice
                    .setAudioFormat(new AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(SR).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                    .setBufferSizeInBytes(Math.max(4096, s.length * 2)).setTransferMode(AudioTrack.MODE_STATIC).build();
            tr.write(s, 0, s.length);
            final Object done = new Object();
            tr.setNotificationMarkerPosition(s.length - 1);
            tr.setPlaybackPositionUpdateListener(new AudioTrack.OnPlaybackPositionUpdateListener() {
                @Override public void onMarkerReached(AudioTrack t) { synchronized (done) { done.notifyAll(); } }
                @Override public void onPeriodicNotification(AudioTrack t) { }
            }, new android.os.Handler(android.os.Looper.getMainLooper()));
            tr.play();
            long total = s.length * 1000L / SR;
            synchronized (done) { done.wait(total + 700); }
            android.util.Log.i("Rosie", "sound " + kind + " (" + style + ") played; wireless=" + wireless + " lead=" + lead * 1000 / SR + "ms head=" + tr.getPlaybackHeadPosition() + "/" + s.length);
            Thread.sleep(wireless ? 120 : 30);
        } catch (Throwable t) {
            android.util.Log.e("Rosie", "sound failed: " + t);
        } finally {
            if (tr != null) { try { tr.stop(); } catch (Exception ignored) { } tr.release(); }
        }
    }

    static boolean wirelessOutput(Context c) {
        try {
            for (android.media.AudioDeviceInfo d : ((android.media.AudioManager) c.getSystemService(Context.AUDIO_SERVICE)).getDevices(android.media.AudioManager.GET_DEVICES_OUTPUTS)) {
                int t = d.getType();
                if (t == android.media.AudioDeviceInfo.TYPE_BLUETOOTH_A2DP || t == android.media.AudioDeviceInfo.TYPE_BLE_HEADSET || t == android.media.AudioDeviceInfo.TYPE_BLUETOOTH_SCO) return true;
            }
        } catch (Exception ignored) { }
        return false;
    }

    static boolean vibrate(Context c) { return !c.getSharedPreferences("assistant", Context.MODE_PRIVATE).getBoolean("novibe", false); }
}
