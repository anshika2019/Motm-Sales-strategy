// Mirrors app/models/schemas.py. Keep in sync by hand if the backend schemas change.

export type AppRole =
  | "admin"
  | "sales_manager"
  | "motm_bd"
  | "motm_sales_engineer"
  | "knowledge_manager";

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RefreshRequest {
  refresh_token: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

// Returned by POST /auth/signup instead of a session -- a self-service
// signup no longer logs the account in immediately, it's created pending
// admin approval. See SignupPage.tsx / AuthContext.tsx's signup().
export interface SignupResponse {
  message: string;
}

// POST /auth/signup only accepts "motm_bd" or "motm_sales_engineer" for
// `role` -- see SIGNUP_ALLOWED_ROLES in app/routers/auth.py. Typed as the
// full AppRole here only for reuse; SignupPage.tsx's picker never offers
// the other three values.
export interface SignupRequest {
  email: string;
  password: string;
  full_name: string;
  role: AppRole;
}

export interface MeResponse {
  id: string;
  email: string;
  full_name: string | null;
  username: string | null;
  created_at: string;
  roles: AppRole[];
}

// Settings page -- see SettingsPage.tsx / PATCH /auth/me in
// app/routers/auth.py. Both optional: an omitted field is left untouched
// server-side (unlike an empty string, which clears it).
export interface UpdateProfileRequest {
  full_name?: string | null;
  username?: string | null;
}

// See PUT /auth/me/email in app/routers/auth.py -- starts Supabase's
// email-change confirmation flow rather than applying immediately.
export interface UpdateEmailRequest {
  email: string;
}

export interface UpdateEmailResponse {
  message: string;
}

// See PUT /auth/me/password in app/routers/auth.py.
export interface UpdatePasswordRequest {
  new_password: string;
}

export interface UpdatePasswordResponse {
  message: string;
}

// Mirrors app/models/schemas.py's UserWithRoles -- one row of the admin
// dashboard's user list (GET /admin/users). See AdminDashboardPage.tsx.
export interface UserWithRoles {
  id: string;
  email: string;
  full_name: string | null;
  created_at: string;
  roles: AppRole[];
  is_approved: boolean;
}

// Mirrors app/models/schemas.py's ApprovalResponse -- POST
// /admin/users/{id}/approve's response.
export interface ApprovalResponse {
  user_id: string;
  is_approved: boolean;
}

export interface CreateConversationResponse {
  id: string;
  persona: "sell_motm" | "support_customer" | "motm_bd";
  title: string | null;
  created_at: string;
}

// Mirrors app/models/schemas.py's BDStrategyRequest. Deliberately lighter
// than StrategyRequest -- BD sells MOTM itself, so there's no required
// website/product pair, only optional prospect context. See
// BDComposer.tsx.
export interface BDStrategyRequest {
  situation?: string;
  raw_message?: string;
  prospect_company?: string;
  prospect_website?: string;
  contact_designation?: string;
  opportunity_stage?: string;
  additional_context?: string;
  trigger?: string;
}

// Mirrors app/models/schemas.py's BDHiringSignalRequest/Response. Two-stage
// agent: signal_analysis is stage 1's raw markdown report (hiring role,
// commercial interpretation, expansion hypothesis, confidence, evidence),
// returned as-is; company_understanding/motm_fit/whatsapp_messages/
// response_handling are stage 2's structured output. See
// HiringSignalComposer.tsx / HiringSignalResultCard.tsx.
export interface BDHiringSignalRequest {
  company_name?: string;
  company_website?: string;
  job_post_text?: string;
  hiring_role?: string;
  location?: string;
  contact_details?: string;
  sender_name?: string;
  notes?: string;
}

export interface BDHiringSignalCompanyUnderstanding {
  products_services: string;
  industries_applications: string;
  typical_buyers: string;
  business_type: string;
}

export interface BDHiringSignalCommercialInterpretation {
  why_hiring: string;
  business_objective: string;
  expansion_opportunity: string;
}

export interface BDHiringSignalMotmFit {
  positioning: string;
  relevant_capabilities: string[];
  key_differentiators: string[];
  why_relevant: string;
}

export interface BDHiringSignalWhatsappMessages {
  message_1: string;
  message_2: string;
  message_3: string;
}

export interface BDHiringSignalResponseHandling {
  send_details: string;
  what_do_you_do: string;
  already_hiring: string;
  not_interested: string;
}

export interface BDHiringSignalResponse {
  id: string;
  signal_analysis: string;
  company_understanding: BDHiringSignalCompanyUnderstanding;
  commercial_interpretation: BDHiringSignalCommercialInterpretation;
  motm_fit: BDHiringSignalMotmFit;
  whatsapp_messages: BDHiringSignalWhatsappMessages;
  response_handling: BDHiringSignalResponseHandling;
  sources: MessageSourceResponse[];
}

export interface StrategyRequest {
  // Required on a conversation's first turn; optional afterwards -- a
  // short/omitted situation with no new website_url/product is treated by
  // the backend as a follow-up on the prior turn's context.
  situation?: string;
  website_url?: string;
  product?: string;
  // The single combined message as typed in the chat composer, before
  // website_url/product extraction. See ChatComposer.tsx.
  raw_message?: string;
  // Set to "generate_pitch" to skip the normal pipeline and generate a
  // pitch from the last strategy turn instead (see _handle_pitch_trigger
  // in app/routers/chat.py). Omit for a normal situation/strategy turn.
  trigger?: string;
}

export interface SituationClassification {
  sales_stage: string;
  problem_type: string;
  buyer_persona: string;
  objective: string;
  missing_information: string[];
}

export interface MessageSourceResponse {
  knowledge_entry_id: string;
  title: string;
  relevance_score: number | null;
}

export interface StrategyResponse {
  id: string;
  company_snapshot: string;
  situation_classification: SituationClassification;
  situation_summary: string;
  whats_probably_going_on: string;
  objective: string;
  recommended_strategy: string[];
  who_to_approach: string;
  questions_to_ask: string[];
  what_to_say: string;
  email_or_whatsapp_draft: string;
  what_not_to_do: string[];
  next_action: string;
  sources: MessageSourceResponse[];
}

export type FeedbackRating = "useful" | "not_useful";

export interface FeedbackRequest {
  rating: FeedbackRating;
  comment?: string | null;
}

export interface FeedbackResponse {
  id: string;
  message_id: string;
  rating: FeedbackRating;
  comment: string | null;
  created_at: string;
}

export interface ConversationSummary {
  id: string;
  persona: "sell_motm" | "support_customer" | "motm_bd";
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface MessageResponse {
  id: string;
  sender: "user" | "assistant";
  content: string;
  created_at: string;
  situation_classification: SituationClassification | null;
  strategy: Omit<StrategyResponse, "id" | "sources" | "situation_classification" | "company_snapshot"> | null;
  narrative: string | null;
  // Populated only for hiring-signal-outreach messages -- see
  // BDHiringSignalResponse (same fields, minus id/sources which live at
  // the top level of this MessageResponse instead).
  hiring_signal: Omit<BDHiringSignalResponse, "id" | "sources"> | null;
  sources: MessageSourceResponse[];
  // Whether a "Generate Pitch" action should be offered after this
  // message. True for a normal completed strategy turn; false for a
  // pitch message itself (message_type "pitch") or a refusal, so the
  // button naturally disappears once a pitch has been generated.
  show_pitch_button: boolean;
}

export interface ConversationMessagesResponse {
  conversation_id: string;
  title: string | null;
  messages: MessageResponse[];
}

// -- SSE streaming event payloads -------------------------------------------

export interface NarrativeChunkEvent {
  delta: string;
}

export interface StreamErrorEvent {
  message: string;
  kind: string;
}

// -- Error shapes -----------------------------------------------------------

export interface QualityGateDetail {
  message: string;
  missing: string[];
}

export interface PydanticValidationItem {
  loc: (string | number)[];
  msg: string;
  type: string;
}

export type ApiError =
  | { kind: "unauthorized"; message: string }
  | { kind: "forbidden"; message: string }
  | { kind: "not_found"; message: string }
  | { kind: "conflict"; message: string }
  | { kind: "quality_gate"; detail: QualityGateDetail }
  | { kind: "bad_url"; message: string }
  | { kind: "validation"; items: PydanticValidationItem[] }
  | { kind: "server"; message: string }
  | { kind: "network"; message: string };
