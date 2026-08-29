OPPORTUNITY_TYPE_CLASSIFICATION_PROMPT = """
You are classifying which ONE opportunity type a B2B sales pitch used, so
a regenerated version of the pitch can be checked for actually using a
different one this time.

Match the pitch's core ask against exactly ONE of these groups:

- "additional_source" -- positions the seller as an additional,
  alternate, or backup manufacturing/supply source (including "overflow
  capacity" or "lead-time risk" framing tied to being a second source).
- "capacity_support" -- focuses on supporting overall production
  capacity or volume, without the "additional/backup source" framing.
- "difficult_components" -- focuses on specific hard-to-source, tight-
  tolerance, or specialty components specifically.
- "cost_review" -- focuses on price comparison, re-quoting, or a
  commercial/cost benchmarking angle.
- "repeat_production" -- focuses on being a source for ongoing/repeat
  production runs.
- "unclear" -- none of the above clearly fits, or the pitch does not
  contain a distinct opportunity angle.

Return ONLY a valid JSON object, no preamble, no explanation:
{"opportunity_type": "additional_source"}

Pitch text:
{pitch_text}
"""
