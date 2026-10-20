# Real-device checklist (Layer 1)

## A. Silent, automatic: Settings > Self-test > Run self-test (20 seconds, no sound, nothing sent)
Checks: Android/app version, PC reachable + logged in, phone link running, microphone returns signal, offline "Hey Rosie" model loads,
listener state + training, battery unrestricted, popup permission, notifications, contacts/texts/calls, notification access, screen control,
assistant role (headphone button), proximity sensor (pocket rule), PC text-to-speech reaches the phone, PC transcription round trip, full voice request.
Each failure says what to tap to fix it. Over a USB cable the same test can be started with `adb shell am start -n app.rosie.assistant/.MainActivity --ez selftest true`
and read with `adb logcat -s Rosie` (lines "selftest PASS/FAIL ...").

## B. Needs you (sound / body): do these once and report what happens
| # | Try | Expected | If it fails, tell me |
|---|---|---|---|
| 1 | Settings > Hey Rosie > Turn on, then Train my voice (6 times, in your normal carry setup) | each attempt shows what it heard; "Saved" | the "I heard" texts |
| 2 | Say "Hey Rosie" with the screen on, phone on a table | short chime, popup, then she listens | what you heard/saw |
| 3 | Same with the phone locked / screen off | chime + vibration, she still answers out loud | does it work at all |
| 4 | Phone in pocket, no headphones | she stays off (no chime, no mic dot) | any wake-up |
| 5 | Phone in pocket, Bluetooth headset on ("Listen through my Bluetooth headset" ON) | wakes through the headset | does it wake; music quality |
| 6 | "Hey Rosie, what time is it" then stay quiet | answer, then ends after ~10 s | |
| 7 | Say something, then tap the popup / notification Stop / headphone button while she speaks | she stops at once | how long it took |
| 8 | "Text Mom hello" with "Read it back" on, then "change it", then "send" | read-back, new wording, sent once | |
| 9 | "Call Mom" | "Calling Mom", dials after ~3 s; saying "cancel" stops it | |
| 10 | "Reply to Sam in WhatsApp: on my way" (needs notification access + a message notification) | read-back, then sent | |
| 11 | "Open WhatsApp and open the chat with Sam" (screen control) | she opens it; a "controlling your screen" notice shows; Stop halts her | |
| 12 | Hold the headphone button | Rosie starts listening (after choosing her as assistant) | |
| 13 | Restart the phone | notification "Tap to turn Hey Rosie back on"; PC link returns by itself | |

