SALES_PITCH_MAIN_PROMPT = """📣 MAIN SALES PITCH (45-90 seconds)

Write a natural spoken pitch the SE can use in a call or meeting.
Not a presentation. Not an email. Something said out loud.

If this is a cold or early-stage situation (no previous interaction), open
by valuing the customer's time (a brief permission-seeking or thank-you
line) BEFORE any product or company description — do not lead with "We
offer" or "We are". Skip this opening line only when there is an
established previous interaction (see SITUATION AWARENESS below).

Cover these parts where the context supports them.
Skip any part the context does not support — do not invent content:

1. WHO WE HELP
   One short sentence about the type of company or situation
   we typically work with. Keep it relevant to this prospect.

2. THE PROBLEM
   What specific problem or risk exists in this situation.
   Only use problems that are confirmed or clearly implied
   by the situation — do not invent generic industry problems.

3. HOW WE HELP
   What the product or service does. Be specific to what is
   being sold. Do not make it sound like a product brochure.

4. WHY IT MATTERS HERE
   Connect it to this specific prospect and situation.
   If the connection is not clear from the context, ask a
   question instead of making an unsupported claim.

5. ONE NEXT STEP
   A simple, low-pressure ask that fits the current sales stage.
   Do not ask for a big commitment if the relationship is early.
   Do not restart from cold if the relationship is established.

SITUATION AWARENESS:
Before writing, check the sales stage.

If this is a negotiation or price discussion:
- Do not reintroduce the product as if it is a first meeting
- Address the current conversation — the price gap, the objection,
  the comparison being made
- The pitch should move the conversation forward, not restart it

If this is a cold or early-stage situation:
- Build from scratch — introduce relevance, problem, solution,
  next step naturally

LANGUAGE RULES:
- Write like a real person talking
- Short sentences
- No corporate phrases:
  - NOT "leveraging our capabilities"
  - NOT "driving operational excellence"
  - NOT "aligning our solutions with your requirements"
  - NOT "protecting your brand reputation"
  - NOT "world-class precision"
- Do not state business outcomes unless confirmed in the situation
- If an outcome is not confirmed, frame it as a question

LENGTH:
60-120 words. Must be deliverable in under 90 seconds.

OUTPUT:
Just the pitch. No section labels. No bullet points.
No explanation before or after.
"""