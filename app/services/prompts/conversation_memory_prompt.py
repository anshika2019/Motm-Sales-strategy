CONVERSATION_MEMORY_PROMPT = """
You maintain a rolling memory summary of an ongoing sales-advisory
conversation between a Sales Engineer and an AI Sales Director, for use as
background context in later turns and in the Sales Engineer's future
conversations.

Given the EXISTING SUMMARY (may be empty, on the first update) and the most
RECENT TURNS of the conversation, write an UPDATED summary in 3 to 5
sentences covering:
- the prospect/company involved and the core situation
- what strategies have already been recommended or tried
- any feedback the user gave on prior responses
- where things currently stand / open next steps

STRICT RULES:
- Overwrite stale or superseded facts rather than appending to them --
  the summary must stay a bounded 3-5 sentences, not grow indefinitely.
- Be factual and concise. No preamble, no headers, no bullet points --
  plain prose only.
- Output ONLY the updated summary text.

EXISTING SUMMARY:
{existing_summary}

RECENT TURNS:
{recent_turns}
"""
