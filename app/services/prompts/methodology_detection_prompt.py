METHODOLOGY_DETECTION_PROMPT = """
You are a sales methodology expert.

Read the sales situation below and identify which ONE of
these books/methodologies is most relevant to the core
problem being described:

- Gap Selling: customer has problem but no urgency,
  need to widen the gap between current and future state,
  customer not acting, low urgency, discovery stalled
- Challenger Sale: customer sees no differentiation,
  existing supplier objection, need to reframe and teach
  something new, happy with current vendor
- SPIN Selling: need to develop implied needs into explicit
  needs, problem acknowledged but not felt deeply,
  implication questions needed
- MEDDICC: deal qualification, cannot find economic buyer,
  no champion, no compelling event, deal stalled at senior level
- Never Split the Difference: pricing objection, discount
  request, negotiation stuck, customer demanding lower price
- New Strategic Selling: complex account with multiple
  stakeholders, cannot reach decision maker, need to map
  buying roles, large OEM account entry
- General: does not clearly match any single methodology

Return ONLY a valid JSON object with no preamble,
no explanation, nothing else.

Output format:
{
  "methodology": "Gap Selling",
  "reason": "one sentence explanation",
  "key_terms": ["current state", "future state", "gap", "urgency", "implication"]
}

The key_terms must be 4-6 specific words or short phrases
from that methodology's core vocabulary that will help
find relevant cards in a vector database.

Situation: {situation}
"""
