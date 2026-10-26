package app.rosie.assistant;

import android.os.Bundle;
import android.service.voice.VoiceInteractionSession;
import android.service.voice.VoiceInteractionSessionService;

public class RosieSessionService extends VoiceInteractionSessionService {
    @Override public VoiceInteractionSession onNewSession(Bundle args) { return new Session(this); }

    static class Session extends VoiceInteractionSession {
        Session(android.content.Context c) { super(c); }
        @Override public void onShow(Bundle args, int flags) {
            super.onShow(args, flags);
            android.content.Intent i = new android.content.Intent(getContext(), AssistTriggerActivity.class).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            try { startAssistantActivity(i); } catch (Exception e) { try { getContext().startActivity(i); } catch (Exception ignored) { } }
            hide();
        }
    }
}
