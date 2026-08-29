PITCH_FEEDBACK_CLASSIFICATION_PROMPT = """
You are classifying a Sales Engineer's follow-up message about a sales
pitch they already received from this tool, to decide whether special
handling is needed before generating the next version.

Classify the message into exactly ONE category:

- "more_detail" -- the message asks for more detail, more length, more
  elaboration, or a more thorough version of the pitch (e.g. "in more
  detail", "in more detailed", "can you expand on this", "make it
  longer", "add more depth").
- "regenerate_different_angle" -- the message says the previous pitch
  didn't work, wasn't well received, got no response, or explicitly asks
  for a different approach/angle (e.g. "it did not work", "try another
  approach", "my customer is not responding", "give me a different
  angle", "that didn't land").
- "none" -- neither of the above -- a new/unrelated request, a request
  for a different channel/section, ordinary conversation, or feedback
  that isn't about detail level or changing the approach.

Return ONLY a valid JSON object, no preamble, no explanation:
{"feedback_type": "more_detail"}

Examples:
"in more detail" -> "more_detail"
"in more detailed" -> "more_detail"
"can you elaborate on this" -> "more_detail"
"it does not work i need another approach" -> "regenerate_different_angle"
"my customer is not responding" -> "regenerate_different_angle"
"try a different angle" -> "regenerate_different_angle"
"give me the WhatsApp version instead" -> "none"
"the customer is a purchase manager" -> "none"

Message: {message}
"""
