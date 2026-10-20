package app.rosie.assistant;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** After a restart: reconnect the phone link, and remind you to open Rosie to turn "Hey Rosie" back on (Android does not let a microphone service start by itself at boot). */
public class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c, Intent i) {
        if (!Intent.ACTION_BOOT_COMPLETED.equals(i.getAction())) return;
        android.content.SharedPreferences p = c.getSharedPreferences("assistant", Context.MODE_PRIVATE);
        if (p.getBoolean("phonectl", false)) { try { c.startForegroundService(new Intent(c, LinkService.class)); } catch (Exception ignored) { } }
        if (p.getBoolean("wake", false)) {
            NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
            nm.createNotificationChannel(new NotificationChannel("wake_heard", "Rosie heard you", NotificationManager.IMPORTANCE_HIGH));
            PendingIntent open = PendingIntent.getActivity(c, 5, new Intent(c, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
            nm.notify(4, new Notification.Builder(c, "wake_heard").setSmallIcon(android.R.drawable.ic_btn_speak_now).setContentTitle("Tap to turn \"Hey Rosie\" back on")
                    .setContentText("The phone restarted. Open Rosie once and she starts listening again.").setContentIntent(open).setAutoCancel(true).build());
        }
    }
}
