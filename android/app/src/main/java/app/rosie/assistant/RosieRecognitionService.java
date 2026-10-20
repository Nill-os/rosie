package app.rosie.assistant;

import android.content.Intent;
import android.speech.RecognitionService;
import android.speech.SpeechRecognizer;

/** Required by Android for assistant apps; Rosie does her own listening, so this does nothing. */
public class RosieRecognitionService extends RecognitionService {
    @Override protected void onStartListening(Intent i, Callback c) { try { c.error(SpeechRecognizer.ERROR_CLIENT); } catch (Exception ignored) { } }
    @Override protected void onCancel(Callback c) { }
    @Override protected void onStopListening(Callback c) { }
}
