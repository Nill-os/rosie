package app.rosie.assistant;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.IBinder;

public class LinkService extends Service {
    private PhoneLink link;
    private Thread thread;

    @Override public IBinder onBind(Intent i) { return null; }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel("link", "Connected to your PC", NotificationManager.IMPORTANCE_MIN));
        PendingIntent open = PendingIntent.getActivity(this, 7, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        Notification n = new Notification.Builder(this, "link").setSmallIcon(android.R.drawable.stat_notify_sync).setContentTitle("Rosie is connected to your PC")
                .setContentText("She can use this phone when you ask.").setContentIntent(open).setOngoing(true).build();
        startForeground(8, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        if (thread == null) { link = new PhoneLink(this); thread = new Thread(link, "phonelink"); thread.start(); }
        return START_STICKY;
    }

    @Override public void onDestroy() { if (link != null) link.stop(); super.onDestroy(); }
}
