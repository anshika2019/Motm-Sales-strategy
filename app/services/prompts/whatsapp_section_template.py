# Section instruction block spliced into PITCH_GENERATION_PROMPT's
# {sections_to_generate} by _PITCH_SECTION_TEMPLATES in app/services/llm.py.
# Deliberately kept OUT of PITCH_GENERATION_PROMPT itself and assembled at
# call time so only the section(s) the user actually asked for are ever
# present in the prompt -- an earlier version kept all three descriptions
# inline with a top note saying "only produce the one(s) named above,
# ignore the rest below," and the model routinely ignored the note and
# generated all three anyway (the same competing-instructions failure mode
# documented on check_company_situation_match() and the followup/strategy
# prompt split: detailed content the model can see always beats a short
# instruction telling it to disregard that content).
WHATSAPP_SECTION_TEMPLATE = """📱 WHATSAPP MESSAGE

Write a concise WhatsApp message (maximum 60 words).

The message must:
- Reflect the CURRENT sales stage and objective.
- Reference the previous interaction or current situation when one exists.
- Mention the product naturally when relevant, but do NOT repeat or re-explain the product if the prospect already knows what is being discussed.
- Sound like a real salesperson sending a personal message, not a marketing template.
- Be conversational, professional and low-pressure.
- Do NOT use generic phrases such as "just checking in" unless followed by a meaningful reason for contacting them.
- Do NOT introduce new claims, statistics, customer examples, technical specifications, or benefits that are not provided in the context.
- Do NOT force a problem statement, value proposition, or proof point.
- End with ONE clear, easy-to-answer next-step question or request.
- The CTA must match the current sales stage.

If the opportunity is already advanced (technical discussion, sample evaluation,
positive test result, RFQ pending, quotation submitted, etc.), focus on moving
the opportunity forward rather than restarting the product pitch.

VERTICAL INTEGRATION:
If the company profile suggests the prospect may manufacture the same
product being sold, do NOT write a standard buyer pitch and do NOT
refuse to write the message. Instead frame it around the most plausible
non-buyer angle (additional/alternate source, overflow capacity, or
specialty tolerance work) and add a short caveat noting this is assumed,
e.g. "assuming this could work as an overflow/second source -- let me
know if not." Keep the whole message, including the caveat, within the
word limit.

Maximum: 60 words."""
