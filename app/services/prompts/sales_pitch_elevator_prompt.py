SALES_PITCH_ELEVATOR_PROMPT = """🛗 ELEVATOR PITCH (20-30 seconds)

Write a short spoken pitch — something the SE can say naturally
in 20-30 seconds. Not an email. Not a paragraph from a brochure.

Answer these four things in flowing, natural spoken sentences:
- Who do we help?
- What problem do we solve?
- How do we solve it?
- What is one realistic next step?

SITUATION AWARENESS:
Before writing, check the sales stage.

If this is a cold or early-stage situation:
- Introduce who you help and what problem you solve
- Keep it relevant to this prospect's likely situation

If this is a negotiation or follow-up situation:
- Do NOT reintroduce the company or product from scratch
- Reference what is already happening
- Focus on the value question being debated right now
- Example: If there is a price objection, the elevator pitch
  should address why the premium exists — not who you are

LANGUAGE RULES:
- Write like a real person talking to another person
- Short sentences only
- No corporate phrases:
  - NOT "safeguarding your brand reputation"
  - NOT "utilizing our specialized process"
  - NOT "align our technical process"
  - NOT "meet the highest standard of field performance"
  - NOT "eliminates expensive warranty rework" unless confirmed
- Only state business outcomes that are confirmed in the situation
- If an outcome is not confirmed, ask about it instead of claiming it

LENGTH:
Maximum 60 words. Must be sayable in under 30 seconds.

OUTPUT:
Just the pitch. No label. No explanation. No bullet points.
"""