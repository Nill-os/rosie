# How Siri and Gemini handle requests and confirmations, and what Rosie copies

Researched from Apple's SiriKit / Human Interface Guidelines, Google's Assistant conversation-design guide, Android Auto / Gemini help pages and Apple support threads.

## What they do
- **Three steps (SiriKit): resolve, confirm, handle.** Fill in anything missing, confirm only when there is something to protect, then do it. Tasks with financial impact require confirmation.
- **Implicit confirmation by default (Google):** restate the key details and say what was done ("Alarm set for 6:45 AM"). No question. Explicit confirmation is rare and kept for things that are hard to undo: sending messages on someone's behalf, payments, deleting data. "Avoid over-confirming routine inputs."
- **Messages:** both read the message back and wait for send / change / cancel. Siri has an optional **Auto-Send** setting (separately for headphones and CarPlay); you can still cancel. Gemini repeats the message and confirms the same way.
- **Calls:** Siri now just dials (people complain about wrong-person calls); when a contact has several numbers it asks "Which one? Mobile or work?". Google announces the contact it is calling.
- **Ambiguity:** one targeted question ("Which one?"), never a list of open questions.
- **Gemini voice:** sends the command automatically when you stop speaking and tries not to cut you off.
- **Always cancellable:** stop / cancel at any time.

## What Rosie does
| Kind of request | Behaviour |
|---|---|
| Notes, alarms, timers, music, flashlight, volume, navigation, searches, tasks, reminders, calendar entries | Just do it, then say what was done with the key details. No question. |
| Text message | Say who and exactly what, then wait for **send / change / cancel**. "Change it" asks what it should say and reads the new wording back. Setting: ask first, auto-send with headphones, or always auto-send (3 second window to say cancel). |
| Call | "Calling Mom", then dial after a 3 second cancel window (or ask first, or dial right away: Settings). |
| Contact matches different people | Ask exactly: "Which one: A or B?" The same person with several numbers: use the mobile. A phone number is only accepted if you said it. |
| Delete, mail cleanup, double-booking, anything that spends money | Always ask first. |
| Stop / cancel | Tap the popup, the notification Stop, the headphone button, or say "stop". |
| Pause length | Settings: quick 0.8 s, normal 1.2 s, patient 2 s. |
