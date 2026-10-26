package app.rosie.assistant;

import android.content.Context;
import android.media.AudioDeviceInfo;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Build;

import java.util.List;

// board #686 quirk-bluetooth-call-mode
public class MicRoute {
    private static int refs = 0;
    private static boolean btOn = false;
    private static int savedMode = AudioManager.MODE_NORMAL;

    static AudioManager am(Context c) { return (AudioManager) c.getSystemService(Context.AUDIO_SERVICE); }

    static AudioDeviceInfo wired(Context c) {
        for (AudioDeviceInfo d : am(c).getDevices(AudioManager.GET_DEVICES_INPUTS)) {
            int t = d.getType();
            if (t == AudioDeviceInfo.TYPE_WIRED_HEADSET || t == AudioDeviceInfo.TYPE_USB_HEADSET) return d;
        }
        return null;
    }

    static AudioDeviceInfo bluetooth(Context c) {
        AudioManager a = am(c);
        if (Build.VERSION.SDK_INT >= 31) {
            List<AudioDeviceInfo> l = a.getAvailableCommunicationDevices();
            for (AudioDeviceInfo d : l) if (d.getType() == AudioDeviceInfo.TYPE_BLUETOOTH_SCO || d.getType() == AudioDeviceInfo.TYPE_BLE_HEADSET) return d;
            return null;
        }
        return a.isBluetoothScoAvailableOffCall() && a.getDevices(AudioManager.GET_DEVICES_OUTPUTS).length > 0 ? bluetoothOut(c) : null;
    }

    private static AudioDeviceInfo bluetoothOut(Context c) {
        for (AudioDeviceInfo d : am(c).getDevices(AudioManager.GET_DEVICES_OUTPUTS)) if (d.getType() == AudioDeviceInfo.TYPE_BLUETOOTH_SCO) return d;
        return null;
    }

    static String describe(Context c, boolean allowBt) {
        AudioDeviceInfo w = wired(c);
        if (w != null) return "wired headset microphone";
        AudioDeviceInfo b = bluetooth(c);
        if (b != null) return allowBt ? "Bluetooth headset: " + b.getProductName() : "phone microphone (Bluetooth headset " + b.getProductName() + " is connected but not used while idle)";
        return "phone microphone";
    }

    static synchronized void acquire(Context c, boolean allowBt) {
        refs++;
        if (refs == 1 && allowBt && wired(c) == null) {
            AudioDeviceInfo b = bluetooth(c);
            if (b != null) startBt(c, b);
        }
    }

    static synchronized void release(Context c) {
        if (refs > 0) refs--;
        if (refs == 0 && btOn) stopBt(c);
    }

    private static void startBt(Context c, AudioDeviceInfo b) {
        try {
            AudioManager a = am(c);
            savedMode = a.getMode();
            a.setMode(AudioManager.MODE_IN_COMMUNICATION);
            if (Build.VERSION.SDK_INT >= 31) a.setCommunicationDevice(b);
            else { a.startBluetoothSco(); a.setBluetoothScoOn(true); }
            btOn = true;
            Thread.sleep(700);  // board #686 why-wait-link-700ms
        } catch (Throwable t) {
            android.util.Log.e("Rosie", "bluetooth mic failed: " + t);
            if (!btOn) { try { am(c).setMode(savedMode); } catch (Throwable ignored) { } }  // board #686 why-restore-audio-mode
        }
    }

    private static void stopBt(Context c) {
        try {
            AudioManager a = am(c);
            if (Build.VERSION.SDK_INT >= 31) a.clearCommunicationDevice(); else { a.setBluetoothScoOn(false); a.stopBluetoothSco(); }
            a.setMode(savedMode);
        } catch (Throwable ignored) { }
        btOn = false;
    }

    static AudioRecord record(Context c, int rate) {
        AudioDeviceInfo dev = wired(c);
        if (dev == null && btOn) {
            for (AudioDeviceInfo d : am(c).getDevices(AudioManager.GET_DEVICES_INPUTS)) if (d.getType() == AudioDeviceInfo.TYPE_BLUETOOTH_SCO || d.getType() == AudioDeviceInfo.TYPE_BLE_HEADSET) dev = d;
        }
        int min = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        AudioRecord r = new AudioRecord(btOn ? MediaRecorder.AudioSource.VOICE_COMMUNICATION : MediaRecorder.AudioSource.VOICE_RECOGNITION, rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(min, rate));
        if (dev != null) r.setPreferredDevice(dev);
        return r;
    }

    static String key(Context c, boolean allowBt) { return describe(c, allowBt); }
}
