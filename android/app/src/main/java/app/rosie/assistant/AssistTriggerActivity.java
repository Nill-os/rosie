package app.rosie.assistant;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;

/** Invisible doorway for "assistant" requests: the headphone button long-press, the power-button/assistant gesture, voice-command from a headset.
 *  It starts Rosie's hands-free conversation (with sound, no screen needed) and closes at once. */
public class AssistTriggerActivity extends Activity {
    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            startForegroundService(new Intent(this, WakeService.class).setAction(WakeService.CONVERSE));
        } else {
            startActivity(new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));      // needs the microphone permission first
        }
        finish();
        overridePendingTransition(0, 0);
    }
}
