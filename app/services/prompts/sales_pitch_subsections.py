# SALES_PITCH_SUBSECTIONS -- the 9 named parts of the "sales pitch" format
# defined by the W2R RAG Addendum (Sales Pitch Generation Instructions),
# keyed to match detect_output_format()'s "sales_pitch_*" values one for
# one. Kept as separate named entries (rather than one monolithic string)
# so a Sales Engineer can ask for a single named section -- e.g. "give me
# the MAIN SALES PITCH" -- and get only that section, not the whole
# document again. Distinct from the three channel templates (whatsapp/
# cold-call/email): those produce ONE piece of ready-to-send outreach copy
# for a named channel; these produce the parts of the sales pitch document
# (value proposition through next-step CTA). Deliberately self-contained
# per sub-section like the channel templates, but does NOT restate the
# global rules that already govern every section of PITCH_GENERATION_PROMPT
# (FACTUAL ACCURACY, PLACEHOLDER RULE, PERSONALIZATION, SELF-INTRODUCTION,
# PERSONA ADAPTATION, KNOWLEDGE CARD RULE, PREVIOUS INTERACTION RULE) --
# those already apply regardless of which section template is spliced in.
# RETIRED — replaced by SALES_PITCH_MERGED_PROMPT
# Kept here temporarily for reference. Safe to delete after testing.
# from app.services.prompts.sales_pitch_cold_call_prompt import SALES_PITCH_COLD_CALL_PROMPT
from app.services.prompts.sales_pitch_merged_prompt import SALES_PITCH_MERGED_PROMPT
from app.services.prompts.sales_pitch_core_value_prompt import SALES_PITCH_CORE_VALUE_PROMPT
from app.services.prompts.sales_pitch_discovery_prompt import SALES_PITCH_DISCOVERY_PROMPT
from app.services.prompts.sales_pitch_elevator_prompt import SALES_PITCH_ELEVATOR_PROMPT
from app.services.prompts.sales_pitch_followup_prompt import SALES_PITCH_FOLLOWUP_PROMPT
from app.services.prompts.sales_pitch_main_prompt import SALES_PITCH_MAIN_PROMPT
from app.services.prompts.sales_pitch_next_step_prompt import SALES_PITCH_NEXT_STEP_PROMPT
from app.services.prompts.sales_pitch_objection_prompt import SALES_PITCH_OBJECTION_PROMPT
from app.services.prompts.sales_pitch_persona_prompt import SALES_PITCH_PERSONA_PROMPT
from app.services.prompts.sales_pitch_meeting_script_prompt import SALES_PITCH_MEETING_SCRIPT_PROMPT
from app.services.prompts.sales_pitch_reengagement_prompt import SALES_PITCH_REENGAGEMENT_PROMPT

SALES_PITCH_SUBSECTIONS: dict[str, str] = {
    "core_value": SALES_PITCH_CORE_VALUE_PROMPT,
    "elevator": SALES_PITCH_ELEVATOR_PROMPT,
    "main": SALES_PITCH_MAIN_PROMPT,
    "cold_call": SALES_PITCH_MERGED_PROMPT,  # was SALES_PITCH_COLD_CALL_PROMPT (retired)
    "persona": SALES_PITCH_PERSONA_PROMPT,
    "discovery": SALES_PITCH_DISCOVERY_PROMPT,
    "followup": SALES_PITCH_FOLLOWUP_PROMPT,
    "objection": SALES_PITCH_OBJECTION_PROMPT,
    "next_step": SALES_PITCH_NEXT_STEP_PROMPT,
    "meeting_script": SALES_PITCH_MEETING_SCRIPT_PROMPT,
    "reengagement": SALES_PITCH_REENGAGEMENT_PROMPT,
}