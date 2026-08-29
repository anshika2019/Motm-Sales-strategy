SALES_PITCH_REENGAGEMENT_PROMPT = """🔄 RE-ENGAGEMENT MESSAGE

Write a short message to restart a conversation that has gone quiet.

This can be a WhatsApp message, a short email, or a call opener
depending on what fits the situation best. Keep it brief.

Structure:
- Reference the last interaction naturally
- Give one simple, honest reason to reconnect
- Ask one easy question they can answer quickly

RULES:
- Do not send a long message to a silent prospect
- Do not apologize for following up
- Do not repeat the full product pitch
- Do not use "just checking in" or "touching base"
- Sound like a real person, not a marketing email
- Maximum 60 words
- Output only the message itself — nothing before or after
"""