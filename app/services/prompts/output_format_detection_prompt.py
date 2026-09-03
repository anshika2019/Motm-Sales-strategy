OUTPUT_FORMAT_DETECTION_PROMPT = """
You are classifying what output format a Sales Engineer is asking for from
an AI Sales Director, based on their latest chat message.

The Sales Pitch document has 9 named sections a Sales Engineer may ask for
individually, by name, after already seeing the full document once:
Core Value Proposition, Elevator Pitch, Main Sales Pitch, 5R Cold Call
Pitch, Persona-Specific Pitches, Discovery Questions, Follow-Up Pitches,
Objection-Based Pitch, Next-Step Request.

Read the message and return ONE of these format values:

- "email_only" -- the message explicitly and ONLY asks for an email
  (e.g. "write me an email", "give me an email for this", "only email").
- "whatsapp_only" -- the message explicitly and ONLY asks for a WhatsApp
  message (e.g. "send a WhatsApp", "write a WhatsApp message", "just the
  WhatsApp text").
- "call_script_only" -- the message explicitly and ONLY asks for a plain
  cold call script or opener, with NO reference to "5R" or to the sales
  pitch document (e.g. "cold call script", "what should I say on the
  call", "call opener", "give me a call script").
- "meeting_script_only" -- the message explicitly asks for a meeting
  script or in-person script (e.g. "give me a meeting script", "script
  for my meeting", "what should I say in the meeting", "meeting opener",
  "script for the visit").
- "reengagement_only" -- the message explicitly asks for a re-engagement
  or revival message (e.g. "give me a re-engagement message", "write a
  revival message", "how do I revive this conversation", "re-engage this
  prospect", "restart this conversation").
- "sales_pitch_full" -- the message asks for "a pitch" / "sales pitch" /
  "pitch for this product" in general, WITHOUT naming a specific channel
  or a specific named section of the pitch document (e.g. "write a
  pitch", "give me a sales pitch for this", "draft a pitch I can use",
  "generate a full sales pitch").
  This is the complete single natural pitch script.
- "sales_pitch_core_value" -- asks specifically for the Core Value
  Proposition (e.g. "give me the core value proposition", "what's our
  value prop").
- "sales_pitch_elevator" -- asks specifically for the Elevator Pitch
  (e.g. "give me the elevator pitch", "elevated pitch", "elevator pitch").
- "sales_pitch_main" -- asks specifically for the Main Sales Pitch (e.g.
  "give me the main sales pitch", "just the main pitch", "main pitch
  script", "generate me a main pitch script").
- "sales_pitch_cold_call" -- asks specifically for the 5R Cold Call Pitch
  from the sales pitch document (e.g. "give me the 5R cold call pitch",
  "the cold call section from that pitch"). Distinct from
  "call_script_only" -- use this one only when the message references the
  pitch document's cold call section or explicitly says "5R".
- "sales_pitch_persona" -- asks specifically for the Persona-Specific
  Pitches.
- "sales_pitch_discovery" -- asks specifically for the Discovery
  Questions.
- "sales_pitch_followup" -- asks specifically for the Follow-Up Pitches.
- "sales_pitch_objection" -- asks specifically for the Objection-Based
  Pitch.
- "sales_pitch_next_step" -- asks specifically for the Next-Step Request /
  CTA.
- "all_formats" -- the message names two or more SPECIFIC channels at once
  (e.g. "give me an email and a WhatsApp message", "send me the email,
  WhatsApp text and call script"). Only use this when at least two of
  email/WhatsApp/call script are explicitly named together -- a bare
  "pitch" request (or a request for one named section of the pitch
  document) is never all_formats.
- "strategy_objection" -- the message reports a specific statement, pushback,
  or objection the customer actually said, and the Sales Engineer wants to
  know how to respond -- but does NOT explicitly ask to draft/write an
  email, WhatsApp message, or script (e.g. "customer said we already have
  suppliers", "they said the price is too high", "purchase says just send
  details", "how do I handle this objection they raised about lead time").
  Distinct from "sales_pitch_objection": that one is used only when the
  message asks BY NAME for the pitch document's Objection-Based Pitch
  section. This one is used when a real objection is being reported and the
  SE wants a way to respond to it, with no reference to the document.
- "strategy_advisory" -- the message asks whether to pursue a prospect, how
  to qualify a situation, or what would make it viable -- a judgment call,
  not a message to send (e.g. "is this a good prospect?", "should I pursue
  this?", "is intermittent usage still viable for this product?", "what
  information should I collect before quoting?" when phrased as "should I
  even bother" rather than a plain checklist ask).
- "strategy_checklist" -- the message asks what questions to ask or what
  information/data to gather, as a plain factual request rather than a
  viability judgment call (e.g. "what should I find out?", "what questions
  should I ask?", "what data do I need before I quote this?").
- "strategy_only" -- the message does not ask for any outreach material at
  all, and does not fit "strategy_objection", "strategy_advisory", or
  "strategy_checklist" above -- it is a general strategic/tactical question
  ("what should I do next?", "who should I approach?"), or an
  INFORMATIONAL question asking to be told/explained something ("tell me
  about X", "what is X", "explain X", "what does X do", "how does X
  work"). Informational questions are not outreach requests even when they
  mention a product/company by name -- the asker wants to be informed
  themselves, not given a message to send to someone else. No email,
  WhatsApp, call script, or pitch should be generated for this case.

RULES:
- "Tell me about X" / "what is X" / "explain X" / "what does X do" is
  ALWAYS "strategy_only", never a pitch format -- these ask the assistant
  to inform the user, not to draft something the user will send to a
  third party. Only classify as a pitch/outreach format when the message
  itself asks to WRITE/DRAFT/GIVE/GENERATE a message, script, or pitch --
  not when it asks to be TOLD or have something EXPLAINED.
- If the message names exactly one channel (email, WhatsApp, call script,
  meeting script, or re-engagement message) and nothing else, pick that
  one *_only value.
- If the message names exactly one of the 9 named sections of the sales
  pitch document (by its name, or a close paraphrase of it), pick that
  section's specific "sales_pitch_*" value -- even if the word "pitch"
  also appears in the message.
  Note: "main pitch script" and "main pitch" are paraphrases of
  "Main Sales Pitch" — classify these as "sales_pitch_main".
  "elevated pitch" and "elevator pitch" are paraphrases of
  "Elevator Pitch" — classify these as "sales_pitch_elevator".
- If the message is a general request for "a pitch" / "sales pitch" with
  no specific channel and no specific named section, use
  "sales_pitch_full" -- this is the default for the word "pitch" used
  generically.
- If the message names two or more specific channels together, use
  "all_formats".
- If the message asks how to pitch, approach, or talk to two or more
  different roles or personas differently -- using words like "vs",
  "versus", "compared to", "differently from", "and also to" -- classify
  as sales_pitch_persona even if the word "persona" is not used and no
  document section is named. This is a request for differentiated persona
  pitches, not a single generic pitch.
- A message that reports a customer's actual objection/pushback and asks
  how to respond, WITHOUT explicitly asking to draft/write/send an email,
  WhatsApp message, or script, and WITHOUT naming the pitch document's
  Objection-Based Pitch section, is "strategy_objection" -- not
  "sales_pitch_objection" and not plain "strategy_only".
- A message asking whether to pursue/qualify a prospect, or what would make
  a situation viable, is "strategy_advisory".
- A message asking what questions to ask or what information/data to
  gather, phrased as a plain factual request rather than a viability
  judgment call, is "strategy_checklist".
- If the message asks no question about outreach material whatsoever, and
  does not fit strategy_objection/strategy_advisory/strategy_checklist
  above, use "strategy_only" -- this is the default for ordinary strategy
  questions.
- Return ONLY a valid JSON object, no preamble, no explanation.

Output format:
{
  "format": "email_only",
  "reason": "one short sentence explaining why"
}

Examples:
"tell me about MOTM" -> "strategy_only"
"tell me about this product" -> "strategy_only"
"what is MOTM" -> "strategy_only"
"what does MOTM do" -> "strategy_only"
"explain MOTM to me" -> "strategy_only"
"how does this product work" -> "strategy_only"
"customer said we already have suppliers" -> "strategy_objection"
"they said the price is too high" -> "strategy_objection"
"purchase says just send details" -> "strategy_objection"
"how do I handle this objection about lead time" -> "strategy_objection"
"Customer says: We have been doing this for 15 years without a problem. What should I say?" -> "strategy_objection"
"Customer says: We don't need this. What should I say?" -> "strategy_objection"
"Customer is saying they are happy with their current setup. How do I respond?" -> "strategy_objection"
'"The equipment will cost more than three years of my painter salary." What should I say?' -> "strategy_objection"
'"What difference does coating efficiency really make to me?" How do I respond?' -> "strategy_objection"
'"We are expanding production next year. Why should I talk to you today?" What should I say?' -> "strategy_objection"
'"We have been doing this for 15 years without a problem." What should I say?' -> "strategy_objection"
'A customer asks: "Why should I automate when my team is doing fine?"' -> "strategy_objection"
"is this a good prospect for us?" -> "strategy_advisory"
"should I pursue this?" -> "strategy_advisory"
"is intermittent usage still viable for this product?" -> "strategy_advisory"
"Who should I approach first — Purchase Manager, Plant Head, or MD?" -> "strategy_advisory"
"Which stakeholder should I contact first?" -> "strategy_advisory"
"Whom should I approach in this account?" -> "strategy_advisory"
"Should I approach Purchase, Design Engineering or Hydraulic Engineering first?" -> "strategy_advisory"
"Should I contact the engineer or the purchase manager first?" -> "strategy_advisory"
"Which department should I approach first?" -> "strategy_advisory"
"what should I find out before quoting?" -> "strategy_checklist"
"what questions should I ask?" -> "strategy_checklist"
"what data do I need before I quote this?" -> "strategy_checklist"
"What information should I collect before preparing an ROI pitch?" -> "strategy_checklist"
"What data should I gather before quoting?" -> "strategy_checklist"
"What should I find out before making a recommendation?" -> "strategy_checklist"
"write a pitch for this" -> "sales_pitch_full"
"give me a sales pitch" -> "sales_pitch_full"
"generate a full sales pitch" -> "sales_pitch_full"
"give me the MAIN SALES PITCH" -> "sales_pitch_main"
"generate me a main pitch script" -> "sales_pitch_main"
"main pitch script" -> "sales_pitch_main"
"give me the main pitch" -> "sales_pitch_main"
"give me the elevator pitch" -> "sales_pitch_elevator"
"elevated pitch" -> "sales_pitch_elevator"
"what's the core value proposition" -> "sales_pitch_core_value"
"give me the 5R cold call pitch" -> "sales_pitch_cold_call"
"cold call script" -> "call_script_only"
"give me a call script" -> "call_script_only"
"what should I say on the call" -> "call_script_only"
"give me a meeting script" -> "meeting_script_only"
"script for my meeting" -> "meeting_script_only"
"what should I say in the meeting" -> "meeting_script_only"
"give me a re-engagement message" -> "reengagement_only"
"write a revival message" -> "reengagement_only"
"how do I revive this conversation" -> "reengagement_only"
"give me the persona pitches" -> "sales_pitch_persona"
"how should i pitch to design engineer vs purchase manager?" -> "sales_pitch_persona"
"what's different when speaking to md vs plant head?" -> "sales_pitch_persona"
"how do i approach the purchase manager differently from the plant head?" -> "sales_pitch_persona"
"give me some discovery questions" -> "sales_pitch_discovery"
"give me the follow-up messages" -> "sales_pitch_followup"
"give me the objection pitch" -> "sales_pitch_objection"
"what's the next step / CTA" -> "sales_pitch_next_step"
"give me an email and a WhatsApp message" -> "all_formats"
"send me the email and call script" -> "all_formats"

Message: {message}
"""