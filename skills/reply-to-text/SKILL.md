---
name: reply-to-text
description: "Use when the user wants to reply to or answer a text, message or notification."
---

1. phone_notifications (or phone_read_messages) to find the message being answered.
2. If the user gave the words, send exactly those. Otherwise write one short reply in their voice from what they said.
3. If the notification can be replied to, call phone_reply_notification with its id and the text. For an SMS, call phone_send_sms with the contact name or the number from the message. For another app without a reply box, use phone_screen_do. An email: draft_email_reply (it only drafts). Every send asks the user to confirm first; never invent a number.
4. Reply only with what is about to be sent. Do not claim it was sent until it was.
