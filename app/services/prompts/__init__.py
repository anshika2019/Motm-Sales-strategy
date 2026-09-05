# Prompt constants for the retrieval-quality pipeline, strategy generation,
# and pitch generation. Each prompt lives in its own module (one file per
# named prompt/template) so it can be reviewed/tuned in isolation without
# touching the LLM call code in app/services/llm.py. This __init__ re-
# exports the full public surface so existing `from app.services.prompts
# import X` call sites don't need to change.
from app.services.prompts.bd_hiring_signal_prompt import BD_HIRING_SIGNAL_ANALYSIS_PROMPT
from app.services.prompts.bd_hiring_signal_outreach_prompt import BD_HIRING_SIGNAL_OUTREACH_PROMPT
from app.services.prompts.bd_methodology_detection_prompt import BD_METHODOLOGY_DETECTION_PROMPT
from app.services.prompts.bd_situation_enrichment_prompt import BD_SITUATION_ENRICHMENT_PROMPT
from app.services.prompts.bd_strategy_narrative_prompt import BD_STRATEGY_NARRATIVE_PROMPT
from app.services.prompts.cold_call_section_template import COLD_CALL_SECTION_TEMPLATE
from app.services.prompts.conversation_memory_prompt import CONVERSATION_MEMORY_PROMPT
from app.services.prompts.email_section_template import EMAIL_SECTION_TEMPLATE
from app.services.prompts.final_verification_layer import FINAL_VERIFICATION_LAYER
from app.services.prompts.followup_continuation_prompt import FOLLOWUP_CONTINUATION_PROMPT
from app.services.prompts.followup_response_prompt import FOLLOWUP_RESPONSE_PROMPT
from app.services.prompts.message_intent_prompt import MESSAGE_INTENT_PROMPT
from app.services.prompts.methodology_detection_prompt import METHODOLOGY_DETECTION_PROMPT
from app.services.prompts.opportunity_type_classification_prompt import (
    OPPORTUNITY_TYPE_CLASSIFICATION_PROMPT,
)
from app.services.prompts.output_format_detection_prompt import OUTPUT_FORMAT_DETECTION_PROMPT
from app.services.prompts.pitch_evaluation_prompt import PITCH_EVALUATION_PROMPT
from app.services.prompts.pitch_feedback_classification_prompt import (
    PITCH_FEEDBACK_CLASSIFICATION_PROMPT,
)
from app.services.prompts.pitch_generation_prompt import PITCH_GENERATION_PROMPT
from app.services.prompts.product_extraction_prompt import PRODUCT_EXTRACTION_PROMPT
from app.services.prompts.query_expansion_prompt import QUERY_EXPANSION_PROMPT
from app.services.prompts.sales_objection_response_prompt import SALES_OBJECTION_RESPONSE_PROMPT
from app.services.prompts.sales_pitch_dual_prompt import SALES_PITCH_DUAL_PROMPT
from app.services.prompts.sales_pitch_golden_rule_footer import _SALES_PITCH_GOLDEN_RULE_FOOTER
from app.services.prompts.sales_pitch_merged_prompt import SALES_PITCH_MERGED_PROMPT
# RETIRED — replaced by SALES_PITCH_MERGED_PROMPT
# Kept here temporarily for reference. Safe to delete after testing.
# from app.services.prompts.sales_pitch_prose_prompt import SALES_PITCH_PROSE_TEMPLATE
from app.services.prompts.sales_pitch_subsections import SALES_PITCH_SUBSECTIONS
from app.services.prompts.situation_enrichment_prompt import SITUATION_ENRICHMENT_PROMPT
from app.services.prompts.strategy_advisory_prompt import STRATEGY_ADVISORY_PROMPT
from app.services.prompts.strategy_checklist_prompt import STRATEGY_CHECKLIST_PROMPT
from app.services.prompts.strategy_narrative_prompt import STRATEGY_NARRATIVE_PROMPT
from app.services.prompts.website_url_extraction_prompt import WEBSITE_URL_EXTRACTION_PROMPT
from app.services.prompts.whatsapp_section_template import WHATSAPP_SECTION_TEMPLATE

__all__ = [
    "BD_HIRING_SIGNAL_ANALYSIS_PROMPT",
    "BD_HIRING_SIGNAL_OUTREACH_PROMPT",
    "BD_METHODOLOGY_DETECTION_PROMPT",
    "BD_SITUATION_ENRICHMENT_PROMPT",
    "BD_STRATEGY_NARRATIVE_PROMPT",
    "COLD_CALL_SECTION_TEMPLATE",
    "CONVERSATION_MEMORY_PROMPT",
    "EMAIL_SECTION_TEMPLATE",
    "FINAL_VERIFICATION_LAYER",
    "FOLLOWUP_CONTINUATION_PROMPT",
    "FOLLOWUP_RESPONSE_PROMPT",
    "MESSAGE_INTENT_PROMPT",
    "METHODOLOGY_DETECTION_PROMPT",
    "OPPORTUNITY_TYPE_CLASSIFICATION_PROMPT",
    "OUTPUT_FORMAT_DETECTION_PROMPT",
    "PITCH_EVALUATION_PROMPT",
    "PITCH_FEEDBACK_CLASSIFICATION_PROMPT",
    "PITCH_GENERATION_PROMPT",
    "PRODUCT_EXTRACTION_PROMPT",
    "QUERY_EXPANSION_PROMPT",
    "SALES_OBJECTION_RESPONSE_PROMPT",
    "SALES_PITCH_DUAL_PROMPT",
    "SALES_PITCH_MERGED_PROMPT",
    # "SALES_PITCH_PROSE_TEMPLATE",  # RETIRED — replaced by SALES_PITCH_MERGED_PROMPT
    "SALES_PITCH_SUBSECTIONS",
    "SITUATION_ENRICHMENT_PROMPT",
    "STRATEGY_ADVISORY_PROMPT",
    "STRATEGY_CHECKLIST_PROMPT",
    "STRATEGY_NARRATIVE_PROMPT",
    "WEBSITE_URL_EXTRACTION_PROMPT",
    "WHATSAPP_SECTION_TEMPLATE",
    "_SALES_PITCH_GOLDEN_RULE_FOOTER",
]
