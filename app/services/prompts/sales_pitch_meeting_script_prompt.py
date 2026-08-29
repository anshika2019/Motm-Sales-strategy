SALES_PITCH_MEETING_SCRIPT_PROMPT = """📋 MEETING SCRIPT

Write a spoken script for a face-to-face meeting or video call.
This is different from a call script — the SE is already in the room
or on a video call with the prospect. Structure it accordingly.

Write it in this order:

OPENING LINE
One natural sentence acknowledging why you are meeting today.
Example: "Thanks for making time — I wanted to pick up where we
left off on the quotation."

CONTEXT LINE
One sentence connecting to the prospect's current situation.
Example: "I know the 18% gap is the main thing on the table."

KEY POINT
The single most important thing to establish in this meeting.
Example: "Before we talk numbers, I want to make sure we're
looking at the same technical requirements."

DIAGNOSTIC QUESTION
One open question that gets them talking.
Example: "Can you walk me through what happens on your floor
when a bore comes back out of spec?"

NEXT STEP ASK
One clear, specific ask to move the deal forward from here.
Example: "Would it make sense to bring your quality lead into
the room for 10 minutes to go through our process validation?"

RULES:
- Write as natural spoken dialogue
- Short sentences with natural pauses between each part
- Do not write paragraphs — keep each part to one or two sentences
- Reference the previous interaction — do not restart from cold
- Do not offer discounts or commercial concessions
- Do not state business outcomes that are not confirmed
- Maximum 100 words total
- Output only the script — no labels, no bullet points,
  no section headers in the final output
"""