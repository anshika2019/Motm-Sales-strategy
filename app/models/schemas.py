from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class AppRole(str, Enum):
    """Kept in sync manually with the `app_role` Postgres enum in
    supabase/migrations/0001_auth_and_roles.sql — there is no automatic
    sync between the two, so update both when a role is added/removed."""

    admin = "admin"
    sales_manager = "sales_manager"
    motm_bd = "motm_bd"
    motm_sales_engineer = "motm_sales_engineer"
    knowledge_manager = "knowledge_manager"


class Persona(str, Enum):
    """Which side of the app a conversation belongs to."""

    sell_motm = "sell_motm"
    support_customer = "support_customer"
    motm_bd = "motm_bd"


class KnowledgePersona(str, Enum):
    """Which side of the app a knowledge_entries row is for. Superset of
    Persona -- "shared" entries are surfaced to both sides."""

    sell_motm = "sell_motm"
    support_customer = "support_customer"
    motm_bd = "motm_bd"
    shared = "shared"


class KnowledgeType(str, Enum):
    principle_card = "principle_card"
    case_card = "case_card"
    objection = "objection"
    positioning = "positioning"
    pricing = "pricing"
    other = "other"


class MessageSender(str, Enum):
    user = "user"
    assistant = "assistant"


class MessageType(str, Enum):
    """Distinguishes what kind of assistant turn a message is -- populated
    only on assistant-sender messages; user rows and the pitch-flow's
    "please provide website/product" filler message stay null."""

    strategy = "strategy"
    pitch = "pitch"
    hiring_signal_outreach = "hiring_signal_outreach"


class FeedbackRating(str, Enum):
    useful = "useful"
    not_useful = "not_useful"


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


class SignupRequest(BaseModel):
    """Self-service signup: a new employee picks their own workspace at
    account-creation time. `role` is typed as AppRole here for reuse, but
    POST /auth/signup only accepts motm_bd or motm_sales_engineer -- see
    SIGNUP_ALLOWED_ROLES in app/routers/auth.py. This is a deliberate,
    narrow exception to the rule that role grants normally require an admin
    (see AssignRoleRequest/require_admin): a brand-new account can only ever
    grant itself one of these two non-privileged workspace roles, never
    admin/sales_manager/knowledge_manager."""

    email: str
    password: str
    full_name: str = Field(min_length=1, max_length=200)
    role: AppRole


class MeResponse(BaseModel):
    id: UUID
    email: str
    full_name: str | None
    username: str | None
    created_at: datetime
    roles: list[AppRole]


class UpdateProfileRequest(BaseModel):
    """Body for PATCH /auth/me -- the Settings page's Name/Username fields.
    Both optional so a request only carrying one still works; omitted
    fields are left untouched (unlike an empty string, which clears the
    field). Email is deliberately NOT here -- it goes through Supabase's
    own change-email flow instead (see UpdateEmailRequest), which requires
    confirmation before it takes effect, so it can't be a plain DB write."""

    full_name: str | None = Field(default=None, max_length=200)
    username: str | None = Field(default=None, max_length=50)


class UpdateEmailRequest(BaseModel):
    email: str


class UpdateEmailResponse(BaseModel):
    message: str


class UpdatePasswordRequest(BaseModel):
    new_password: str = Field(min_length=8)


class UpdatePasswordResponse(BaseModel):
    message: str


class UserWithRoles(BaseModel):
    id: UUID
    email: str
    full_name: str | None
    created_at: datetime
    roles: list[AppRole]
    is_approved: bool


class SignupResponse(BaseModel):
    """Returned by POST /auth/signup instead of a session -- self-service
    signups no longer log the new account in immediately (see
    SIGNUP_ALLOWED_ROLES's docstring / login()'s approval check in
    app/routers/auth.py): the account is created pending admin approval,
    and POST /auth/login will 403 until an admin approves it."""

    message: str


class ApprovalResponse(BaseModel):
    user_id: UUID
    is_approved: bool


class AssignRoleRequest(BaseModel):
    role: AppRole


class RoleGrantResponse(BaseModel):
    id: UUID
    user_id: UUID
    role: AppRole
    granted_by: UUID | None
    granted_at: datetime


class RoleRevokeResponse(BaseModel):
    user_id: UUID
    role: AppRole
    revoked_at: datetime
    revoked_by: UUID


class CreateConversationResponse(BaseModel):
    id: UUID
    persona: Persona
    title: str | None
    created_at: datetime


class PostMessageRequest(BaseModel):
    content: str


class MessageSourceResponse(BaseModel):
    knowledge_entry_id: UUID
    title: str
    relevance_score: float | None


class PostMessageResponse(BaseModel):
    id: UUID
    content: str
    sources: list[MessageSourceResponse]


class StrategyRequest(BaseModel):
    website_url: str | None = None
    product: str | None = None
    # Required on a conversation's first turn (enforced in the router, not
    # here) but optional on later turns: a short/omitted situation with no
    # new website_url/product is treated as a follow-up on the prior turn's
    # context rather than a fresh full pipeline pass.
    situation: str | None = None
    # The single combined message as typed by the user in the chat composer,
    # before the frontend's website_url/product extraction. website_url and
    # product may already be embedded in this text -- see
    # SITUATION_ENRICHMENT_PROMPT in app/services/prompts.py.
    raw_message: str | None = None
    # Set to "generate_pitch" by the frontend's pitch button to skip the
    # normal pipeline and generate a pitch from the last strategy turn
    # instead (see _handle_pitch_trigger in app/routers/chat.py). Any other
    # value or None runs the normal flow -- not a strict enum on purpose,
    # matching this model's existing permissive style.
    trigger: str | None = None


class BDStrategyRequest(BaseModel):
    """Input shape for MOTM's own Business Development pipeline (persona=
    motm_bd) -- deliberately much lighter than StrategyRequest: BD is
    selling MOTM itself, not analyzing a prospect's own website/product, so
    there is no required website/product pair to scrape. situation is the
    only required field (enforced in the router, not here, on a
    conversation's first turn -- mirrors StrategyRequest.situation), and
    every "who/where/what stage" detail below is optional context that, if
    given, sharpens retrieval/classification but is never blocking."""

    # Required on a conversation's first turn (enforced in the router) but
    # optional on later turns -- same follow-up-reuse rule as
    # StrategyRequest.situation.
    situation: str | None = None
    # The single combined message as typed by the user in the chat
    # composer, before any field extraction -- mirrors
    # StrategyRequest.raw_message.
    raw_message: str | None = None
    prospect_company: str | None = None
    # Optional: only when this is supplied does the BD pipeline attempt a
    # (lightweight, non-blocking) website lookup -- see
    # app/routers/bd_chat.py's pre-generation pipeline. Unlike SE's
    # website_url, a missing/failed lookup here never blocks the pipeline.
    prospect_website: str | None = None
    contact_designation: str | None = None
    opportunity_stage: str | None = None
    additional_context: str | None = None
    # Same pitch-trigger mechanism as StrategyRequest.trigger -- set to
    # "generate_pitch" by the frontend's pitch button.
    trigger: str | None = None


class BDHiringSignalRequest(BaseModel):
    """Input for the Hiring-Signal Outreach Agent (POST /bd-chat/.../
    hiring-signal-outreach): given a company that's publicly hiring a
    sales/BD/technical-sales-type role, infer the commercial objective
    behind that hire and produce a WhatsApp outreach sequence. Per the
    "MOTM BD Agent Instructions" spec this is driven by, no single field is
    required -- "use available information intelligently" -- but the
    router rejects a request with nothing usable at all (enforced there,
    not here, the same way BDStrategyRequest.situation is)."""

    company_name: str | None = None
    company_website: str | None = None
    # LinkedIn/job-post text pasted in directly, not a URL to fetch --
    # there is no scraping step for this field.
    job_post_text: str | None = None
    hiring_role: str | None = None
    location: str | None = None
    contact_details: str | None = None
    sender_name: str | None = None
    notes: str | None = None


class BDHiringSignalCompanyUnderstanding(BaseModel):
    products_services: str
    industries_applications: str
    typical_buyers: str
    business_type: str


class BDHiringSignalCommercialInterpretation(BaseModel):
    """A concise, visible summary of stage 1's key finding -- extracted
    (not re-derived) from signal_analysis by stage 2, so a BD rep sees the
    "why are they hiring / what does it mean" reasoning up front instead of
    only inside the collapsed full analysis report."""

    why_hiring: str
    business_objective: str
    expansion_opportunity: str


class BDHiringSignalMotmFit(BaseModel):
    positioning: str
    relevant_capabilities: list[str]
    key_differentiators: list[str]
    why_relevant: str


class BDHiringSignalWhatsappMessages(BaseModel):
    message_1: str
    message_2: str
    message_3: str


class BDHiringSignalResponseHandling(BaseModel):
    send_details: str
    what_do_you_do: str
    already_hiring: str
    not_interested: str


class BDHiringSignalResponse(BaseModel):
    """Two-stage output: signal_analysis is stage 1's raw markdown report
    (BD_HIRING_SIGNAL_ANALYSIS_PROMPT -- hiring role, commercial
    interpretation, expansion hypothesis, confidence, evidence
    classification), returned as-is rather than forced into a rigid
    sub-schema since it's already a well-formed report in its own right.
    company_understanding/motm_fit/whatsapp_messages/response_handling are
    stage 2's structured JSON output (BD_HIRING_SIGNAL_OUTREACH_PROMPT),
    which takes signal_analysis as its input and writes the actual
    outreach -- see generate_bd_hiring_signal_analysis()/
    generate_bd_hiring_signal_outreach() in app/services/llm.py. Not shaped
    like StrategyResponse -- this is a fixed multi-part output, not a
    variant of the general strategy shape."""

    id: UUID
    signal_analysis: str
    company_understanding: BDHiringSignalCompanyUnderstanding
    commercial_interpretation: BDHiringSignalCommercialInterpretation
    motm_fit: BDHiringSignalMotmFit
    whatsapp_messages: BDHiringSignalWhatsappMessages
    response_handling: BDHiringSignalResponseHandling
    sources: list[MessageSourceResponse]


class SituationClassification(BaseModel):
    """Mirrors the dict shape returned by classify_situation() in
    app/services/llm.py -- keep both in sync if either changes."""

    sales_stage: str
    problem_type: str
    buyer_persona: str
    objective: str
    missing_information: list[str]


class StrategyResponse(BaseModel):
    """Mirrors the structured sections defined in the system prompt in
    app/services/llm.py (SITUATION SUMMARY, WHAT IS PROBABLY GOING ON, etc.)
    -- keep both in sync if either changes."""

    id: UUID
    company_snapshot: str
    situation_classification: SituationClassification
    situation_summary: str
    whats_probably_going_on: str
    objective: str
    recommended_strategy: list[str]
    who_to_approach: str
    questions_to_ask: list[str]
    what_to_say: str
    email_or_whatsapp_draft: str
    what_not_to_do: list[str]
    next_action: str
    sources: list[MessageSourceResponse]
    show_pitch_button: bool = True


class FeedbackRequest(BaseModel):
    rating: FeedbackRating
    comment: str | None = None


class FeedbackResponse(BaseModel):
    id: UUID
    message_id: UUID
    rating: FeedbackRating
    comment: str | None
    created_at: datetime


class ConversationSummaryResponse(BaseModel):
    id: UUID
    persona: Persona
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageResponse(BaseModel):
    """One row of conversation history for GET .../messages. strategy,
    narrative, and situation_classification are populated only for
    assistant messages produced by the /strategy or /strategy/stream
    pipelines (sourced from Message.pipeline_metadata); assistant messages
    from the legacy /messages endpoint have pipeline_metadata = None, so
    all three stay None."""

    id: UUID
    sender: MessageSender
    content: str
    created_at: datetime
    situation_classification: SituationClassification | None = None
    strategy: dict | None = None
    narrative: str | None = None
    # Populated only for assistant messages produced by the hiring-signal
    # pipeline (message_type=hiring_signal_outreach) -- mirrors
    # BDHiringSignalResponse's shape as a plain dict (sourced from
    # Message.pipeline_metadata's "hiring_signal" key), same pattern as
    # `strategy` above.
    hiring_signal: dict | None = None
    sources: list[MessageSourceResponse] = []
    show_pitch_button: bool = True


class ConversationMessagesResponse(BaseModel):
    conversation_id: UUID
    title: str | None
    messages: list[MessageResponse]


class PitchEvaluationRuleResult(BaseModel):
    id: str
    status: str
    reason: str


class PitchEvaluationResponse(BaseModel):
    """LLM-as-judge audit of one generated pitch Message against the W2R
    RAG Addendum rubric (see PITCH_EVALUATION_PROMPT). Written by a
    background task after the pitch itself is generated -- see
    _evaluate_pitch_background in app/routers/chat.py."""

    id: UUID
    message_id: UUID
    conversation_id: UUID
    output_format: str
    rules: list[PitchEvaluationRuleResult]
    overall_score: int
    top_gaps: list[str]
    created_at: datetime


class PitchEvaluationSummary(BaseModel):
    """One row of the admin cross-conversation compliance listing --
    lighter than PitchEvaluationResponse (no per-rule breakdown)."""

    id: UUID
    message_id: UUID
    conversation_id: UUID
    output_format: str
    overall_score: int
    top_gaps: list[str]
    created_at: datetime
