import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Conversation,
    Feedback,
    KnowledgeEntry,
    LlmCallLog,
    Message,
    MessageSource,
    PitchEvaluation,
    Profile,
)
from app.db.session import async_session_factory, get_db_session
from app.dependencies.auth import CurrentUser
from app.dependencies.roles import require_role
from app.models.schemas import (
    AppRole,
    ConversationMessagesResponse,
    ConversationRenameRequest,
    ConversationSummaryResponse,
    CreateConversationResponse,
    FeedbackRating,
    FeedbackRequest,
    FeedbackResponse,
    KnowledgePersona,
    MessageResponse,
    MessageSender,
    MessageSourceResponse,
    MessageType,
    Persona,
    PitchEvaluationResponse,
    PitchEvaluationRuleResult,
    PostMessageRequest,
    PostMessageResponse,
    SituationClassification,
    StrategyRequest,
    StrategyResponse,
)
from app.services.embeddings import embed_texts
from app.services.knowledge import fetch_known_problem_types
from app.services.llm import (
    _MERGED_PITCH_FORMATS,
    _PITCH_SECTION_TEMPLATES,
    OPPORTUNITY_TYPE_LABELS,
    check_company_situation_match,
    classify_message_intent,
    classify_pitch_feedback,
    classify_pitch_opportunity_type,
    classify_situation,
    detect_methodology,
    detect_output_format,
    enrich_situation,
    expand_queries,
    extract_company_name,
    extract_product_from_text,
    extract_website_url_from_text,
    format_company_snapshot,
    format_context,
    generate_answer,
    generate_narrative_strategy,
    generate_verified_pitch,
    generate_strategy,
    summarize_company,
    summarize_conversation_memory,
)
from app.services.scraper import UnsafeUrlError, fetch_company_pages
from app.services.usage_tracking import get_recorded_usage, start_usage_tracking

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

require_sales_engineer = require_role(AppRole.motm_sales_engineer)

_TOP_N_SOURCES = 5

# Cosine distance (lower = more similar). Above this, treat the retrieval
# as "nothing relevant found" rather than force a strategy out of weak
# matches. Tune based on real query/card relevance once in use.
_NOT_RELEVANT_DISTANCE = 0.8

# A short/omitted situation with no new website_url/product on a
# conversation that already completed one full pipeline pass is treated as
# a follow-up ("draft an email for this", "what about pricing?") rather
# than a fresh situation requiring a full re-scrape/re-classify.
_FOLLOWUP_WORD_THRESHOLD = 15
# Trigger phrases only count within this many words -- unqualified, common
# words like "also"/"now"/"draft" could otherwise misclassify a genuinely
# new, long situation description that happens to contain one of them.
_FOLLOWUP_PHRASE_WORD_BUFFER = 25

_FOLLOWUP_TRIGGER_PHRASES = (
    "write the", "draft", "give me", "now", "also", "what about",
    "can you", "just the", "only the", "summarize", "shorter", "simpler",
)


@router.post(
    "/conversations",
    response_model=CreateConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    current_user: CurrentUser = Depends(require_sales_engineer),
    session: AsyncSession = Depends(get_db_session),
) -> CreateConversationResponse:
    # Long-term memory: seed a brand-new conversation with the summary from
    # the user's most recently updated other conversation, if any, so
    # context carries across separate sessions (not just within one).
    prior_summary_result = await session.execute(
        select(Conversation.memory_summary)
        .where(
            Conversation.user_id == current_user.id,
            Conversation.memory_summary.isnot(None),
        )
        .order_by(Conversation.updated_at.desc())
        .limit(1)
    )
    seed_summary = prior_summary_result.scalar_one_or_none()

    conversation = Conversation(
        user_id=current_user.id, persona=Persona.sell_motm, memory_summary=seed_summary
    )
    session.add(conversation)
    await session.commit()

    return CreateConversationResponse(
        id=conversation.id,
        persona=conversation.persona,
        title=conversation.title,
        created_at=conversation.created_at,
    )


@router.get(
    "/conversations",
    response_model=list[ConversationSummaryResponse],
)
async def list_conversations(
    current_user: CurrentUser = Depends(require_sales_engineer),
    session: AsyncSession = Depends(get_db_session),
) -> list[ConversationSummaryResponse]:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.user_id == current_user.id)
        .order_by(Conversation.updated_at.desc())
        .limit(50)
    )
    return [
        ConversationSummaryResponse(
            id=c.id,
            persona=c.persona,
            title=c.title,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in result.scalars().all()
    ]


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationSummaryResponse,
)
async def rename_conversation(
    conversation_id: UUID,
    body: ConversationRenameRequest,
    current_user: CurrentUser = Depends(require_sales_engineer),
    session: AsyncSession = Depends(get_db_session),
) -> ConversationSummaryResponse:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    conversation.title = body.title.strip()
    await session.commit()

    return ConversationSummaryResponse(
        id=conversation.id,
        persona=conversation.persona,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation(
    conversation_id: UUID,
    current_user: CurrentUser = Depends(require_sales_engineer),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    await session.delete(conversation)
    await session.commit()


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationMessagesResponse,
)
async def get_conversation_messages(
    conversation_id: UUID,
    current_user: CurrentUser = Depends(require_sales_engineer),
    session: AsyncSession = Depends(get_db_session),
) -> ConversationMessagesResponse:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.hidden.is_(False))
        .order_by(Message.created_at.asc())
    )
    messages = result.scalars().all()
    message_ids = [m.id for m in messages]

    sources_by_message: dict[UUID, list[MessageSourceResponse]] = {}
    if message_ids:
        source_rows = await session.execute(
            select(MessageSource, KnowledgeEntry)
            .join(KnowledgeEntry, MessageSource.knowledge_entry_id == KnowledgeEntry.id)
            .where(MessageSource.message_id.in_(message_ids))
        )
        for source, entry in source_rows.all():
            sources_by_message.setdefault(source.message_id, []).append(
                MessageSourceResponse(
                    knowledge_entry_id=entry.id,
                    title=entry.title,
                    relevance_score=source.relevance_score,
                )
            )

    message_responses = []
    for m in messages:
        # pipeline_metadata is None for legacy /messages-endpoint rows --
        # classification/strategy/narrative all stay None for those.
        metadata = m.pipeline_metadata or {}
        classification = metadata.get("classification")
        message_responses.append(
            MessageResponse(
                id=m.id,
                sender=m.sender,
                content=m.content,
                created_at=m.created_at,
                situation_classification=(
                    SituationClassification(
                        sales_stage=classification.get("sales_stage", "unknown"),
                        problem_type=classification.get("problem_type", "unknown"),
                        buyer_persona=classification.get("buyer_persona", "unknown"),
                        objective=classification.get("objective", ""),
                        missing_information=classification.get("missing_information", []),
                    )
                    if classification
                    else None
                ),
                strategy=metadata.get("strategy"),
                narrative=metadata.get("narrative"),
                sources=sources_by_message.get(m.id, []),
            )
        )

    return ConversationMessagesResponse(
        conversation_id=conversation_id, title=conversation.title, messages=message_responses
    )


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/evaluation",
    response_model=PitchEvaluationResponse,
)
async def get_pitch_evaluation(
    conversation_id: UUID,
    message_id: UUID,
    current_user: CurrentUser = Depends(require_sales_engineer),
    session: AsyncSession = Depends(get_db_session),
) -> PitchEvaluationResponse:
    """Returns the W2R-rubric compliance report for one generated pitch
    message (see PITCH_EVALUATION_PROMPT / evaluate_pitch()). The report is
    written by a background task after the pitch is generated, so a 404
    here can mean either the message isn't a pitch the user owns, or the
    background evaluation simply hasn't finished yet -- callers should
    treat it as "not ready", not as a hard failure."""
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    result = await session.execute(
        select(PitchEvaluation).where(
            PitchEvaluation.conversation_id == conversation_id,
            PitchEvaluation.message_id == message_id,
        )
    )
    evaluation = result.scalar_one_or_none()
    if evaluation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No evaluation available yet for this message",
        )

    return PitchEvaluationResponse(
        id=evaluation.id,
        message_id=evaluation.message_id,
        conversation_id=evaluation.conversation_id,
        output_format=evaluation.output_format,
        rules=[PitchEvaluationRuleResult(**rule) for rule in evaluation.rubric_results],
        overall_score=evaluation.overall_score,
        top_gaps=evaluation.top_gaps,
        created_at=evaluation.created_at,
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    response_model=FeedbackResponse,
)
async def submit_feedback(
    conversation_id: UUID,
    message_id: UUID,
    body: FeedbackRequest,
    current_user: CurrentUser = Depends(require_sales_engineer),
    session: AsyncSession = Depends(get_db_session),
) -> FeedbackResponse:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    message = await session.get(Message, message_id)
    if (
        message is None
        or message.conversation_id != conversation_id
        or message.sender != MessageSender.assistant
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    # Upsert: a user can change their thumbs rating on a message. Relies on
    # the feedback_message_user_unique index (message_id, user_id).
    stmt = (
        pg_insert(Feedback)
        .values(
            message_id=message_id,
            user_id=current_user.id,
            rating=body.rating,
            comment=body.comment,
        )
        .on_conflict_do_update(
            index_elements=["message_id", "user_id"],
            set_={"rating": body.rating, "comment": body.comment},
        )
        .returning(Feedback)
    )
    result = await session.execute(stmt)
    await session.commit()
    row = result.scalar_one()

    return FeedbackResponse(
        id=row.id,
        message_id=row.message_id,
        rating=row.rating,
        comment=row.comment,
        created_at=row.created_at,
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=PostMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_message(
    conversation_id: UUID,
    body: PostMessageRequest,
    current_user: CurrentUser = Depends(require_sales_engineer),
    session: AsyncSession = Depends(get_db_session),
) -> PostMessageResponse:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    start_usage_tracking()

    user_message = Message(
        conversation_id=conversation_id, sender=MessageSender.user, content=body.content
    )
    session.add(user_message)

    [query_embedding] = embed_texts([body.content])
    distance = KnowledgeEntry.embedding.cosine_distance(query_embedding).label("distance")
    result = await session.execute(
        select(KnowledgeEntry, distance)
        .where(
            KnowledgeEntry.persona.in_([KnowledgePersona.sell_motm, KnowledgePersona.shared]),
            KnowledgeEntry.is_active.is_(True),
        )
        .order_by(distance)
        .limit(_TOP_N_SOURCES)
    )
    retrieved = result.all()  # list of (KnowledgeEntry, distance) rows

    answer = await generate_answer(body.content, [entry for entry, _ in retrieved])

    assistant_message = Message(
        conversation_id=conversation_id, sender=MessageSender.assistant, content=answer
    )
    session.add(assistant_message)
    await session.flush()  # assigns assistant_message.id for the sources below

    sources = [
        MessageSource(
            message_id=assistant_message.id,
            knowledge_entry_id=entry.id,
            relevance_score=entry_distance,
        )
        for entry, entry_distance in retrieved
    ]
    session.add_all(sources)
    await _persist_usage_logs(session, conversation_id, assistant_message.id, current_user.id)
    await session.commit()

    return PostMessageResponse(
        id=assistant_message.id,
        content=answer,
        sources=[
            MessageSourceResponse(
                knowledge_entry_id=entry.id, title=entry.title, relevance_score=entry_distance
            )
            for entry, entry_distance in retrieved
        ],
    )


_PER_QUERY_CANDIDATES = 8
# Lower than the retrieval candidate pool (_PER_QUERY_CANDIDATES) so
# generate_strategy()/generate_narrative_strategy()'s duplicated knowledge-
# card context stays comfortably bounded (prompt size, cost, latency).
_STRATEGY_TOP_N = 8


_SE_KNOWLEDGE_PERSONAS = (KnowledgePersona.sell_motm, KnowledgePersona.shared)


async def _retrieve_cards(
    session: AsyncSession,
    query_embeddings: list[list[float]],
    rerank_embedding: list[float],
    top_n: int = _TOP_N_SOURCES,
    personas: tuple[KnowledgePersona, ...] = _SE_KNOWLEDGE_PERSONAS,
):
    """Multi-query retrieval: runs a vector search per expanded query
    (expand_queries() in app/services/llm.py turns one situation into
    several specific angles), merges results keeping each card's best
    (lowest) distance across queries, then reranks the deduplicated set
    by semantic distance to rerank_embedding (the enriched situation).

    personas defaults to the SE persona filter (sell_motm + shared) so
    every existing call site in this file is unaffected -- app/routers/
    bd_chat.py passes [motm_bd, shared] instead to scope BD's retrieval to
    its own knowledge base without duplicating this whole function."""
    base_filters = (
        KnowledgeEntry.persona.in_(personas),
        KnowledgeEntry.is_active.is_(True),
    )

    best_by_id: dict = {}
    for embedding in query_embeddings:
        distance = KnowledgeEntry.embedding.cosine_distance(embedding).label("distance")
        result = await session.execute(
            select(KnowledgeEntry, distance)
            .where(*base_filters)
            .order_by(distance)
            .limit(_PER_QUERY_CANDIDATES)
        )
        for entry, entry_distance in result.all():
            existing = best_by_id.get(entry.id)
            if existing is None or entry_distance < existing[1]:
                best_by_id[entry.id] = (entry, entry_distance)

    if not best_by_id:
        return []

    rerank_distance = KnowledgeEntry.embedding.cosine_distance(rerank_embedding).label("distance")
    reranked = await session.execute(
        select(KnowledgeEntry, rerank_distance)
        .where(KnowledgeEntry.id.in_(best_by_id.keys()))
        .order_by(rerank_distance)
        .limit(top_n)
    )
    return reranked.all()


async def _load_prior_pipeline_context(session: AsyncSession, conversation_id: UUID) -> dict | None:
    """The latest assistant Message's pipeline_metadata in this
    conversation, or None if no /strategy turn has completed yet."""
    result = await session.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.sender == MessageSender.assistant,
            Message.pipeline_metadata.isnot(None),
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    msg = result.scalar_one_or_none()
    return msg.pipeline_metadata if msg else None


async def _load_last_strategy_message(
    session: AsyncSession, conversation_id: UUID
) -> Message | None:
    """Most recent assistant message_type='strategy' row -- source content
    for a pitch-button-triggered pitch. Distinct from
    _load_prior_pipeline_context: that fetches pipeline_metadata (session
    context) and is unaffected by message_type since pitch messages always
    carry pipeline_metadata=None (see _build_pitch_message below)."""
    result = await session.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.sender == MessageSender.assistant,
            Message.message_type == MessageType.strategy,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _load_last_assistant_message_type(
    session: AsyncSession, conversation_id: UUID
) -> MessageType | None:
    """Most recent assistant message's type, regardless of what it is --
    used by resolve_output_format() to detect "the last thing we sent was
    a pitch" so a short elliptical follow-up ("more detail") can be routed
    back into pitch generation instead of the unrelated strategy path.
    Distinct from _load_last_strategy_message (filters to type='strategy'
    specifically) and _load_prior_pipeline_context (filters to
    pipeline_metadata IS NOT NULL, which pitch messages never have)."""
    result = await session.execute(
        select(Message.message_type)
        .where(
            Message.conversation_id == conversation_id,
            Message.sender == MessageSender.assistant,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _looks_like_followup_text(situation_raw: str) -> bool:
    word_count = len(situation_raw.split())
    if word_count < _FOLLOWUP_WORD_THRESHOLD:
        return True
    if word_count <= _FOLLOWUP_PHRASE_WORD_BUFFER:
        lowered = situation_raw.lower()
        return any(phrase in lowered for phrase in _FOLLOWUP_TRIGGER_PHRASES)
    return False


def _is_followup(situation_raw: str, website_url: str, product: str, prior_context: dict | None) -> bool:
    if prior_context is None:
        return False  # first turn always runs the full pipeline
    if website_url or product:
        return False  # explicit fresh context supplied -> treat as a new full pass
    return _looks_like_followup_text(situation_raw)


def _is_focused_followup(situation_raw: str, is_followup: bool) -> bool:
    """Stricter than is_followup: gates the retrieval-skip + minimal-answer
    prompt. is_followup AND under 15 words (website_url absence already
    guaranteed by is_followup==True)."""
    return is_followup and len(situation_raw.split()) < _FOLLOWUP_WORD_THRESHOLD


def _is_formatting_only_followup(situation_raw: str) -> bool:
    """True when a follow-up message is asking to reshape/trim the PREVIOUS
    output ("make it shorter", "just the email", "draft a whatsapp
    version") rather than supplying new facts about the situation. Reuses
    _FOLLOWUP_TRIGGER_PHRASES since those are the same meta-instruction
    phrases used elsewhere to recognize a follow-up in the first place.

    This is distinct from word count: a short message can still be
    substantive ("customer is not responding" is 4 words but is new
    situational information, not a formatting request), so callers must
    not use _is_focused_followup (word-count-only) to decide whether to
    re-run classification -- see _prepare_direct_pitch_intent()."""
    lowered = situation_raw.lower()
    return any(phrase in lowered for phrase in _FOLLOWUP_TRIGGER_PHRASES)


def _merge_followup_situation(prior_context: dict, situation_raw: str) -> dict:
    """Folds a follow-up turn's new raw text into the frozen enriched
    situation from prior_context (set at the first turn that ran the full
    pipeline), so it becomes part of the primary SALES SITUATION context
    the model reasons over -- not just a side-channel instruction. Returns
    a new dict; prior_context is never mutated in place since it may still
    back the persisted pipeline_metadata of an earlier message.

    Without this, a substantive follow-up like "the customer stopped
    responding" only ever reaches the model via the {latest_request}
    placeholder in PITCH_GENERATION_PROMPT, while the much more load-
    bearing {situation} field (and the sales-stage classification derived
    from it) stays stuck on turn-1's text -- producing near-duplicate
    output on every follow-up (see _prepare_direct_pitch_intent())."""
    if not situation_raw:
        return prior_context
    merged_situation = f"{prior_context.get('enriched_situation', '')} Follow-up: {situation_raw}"
    return {**prior_context, "enriched_situation": merged_situation}


_PITCH_TRIGGER = "generate_pitch"


async def resolve_output_format(
    session: AsyncSession, conversation_id: UUID, intent_text: str, is_followup: bool
) -> str:
    """Wraps detect_output_format() with a fast path for empty/near-empty
    text (e.g. a follow-up turn with no new message) -- no need to spend an
    LLM call classifying an empty string, and "strategy_only" is the
    correct default for that case (falls through to the normal pipeline,
    matching pre-existing behavior).

    detect_output_format() classifies the CURRENT message in isolation --
    it has no memory of what the previous assistant turn was. A short
    elliptical follow-up like "more detail" or "make it shorter" mentions
    no channel/pitch keyword at all, so it classifies as "strategy_only"
    even when it's clearly asking to continue/adjust the pitch that was
    just generated -- which then routes into the unrelated strategy/
    follow-up narrative path instead of generate_pitch(), producing a
    completely different response shape (see the "more detail" bug: it
    replaced the labeled pitch script with generic prose).

    is_followup (the message looks like a short continuation, not a new
    situation) plus a "strategy_only" classification plus the most recent
    assistant message actually being a pitch is the specific ambiguous
    case this guards -- override to "sales_pitch_full" so the message
    still reaches generate_pitch(), where {latest_request} (see
    _build_pitch_context()) carries the actual instruction text (e.g.
    "more detail") into the model."""
    if not intent_text.strip():
        return "strategy_only"
    detected = await detect_output_format(intent_text)
    if detected == "strategy_only" and is_followup:
        last_type = await _load_last_assistant_message_type(session, conversation_id)
        if last_type == MessageType.pitch:
            return "sales_pitch_full"
    return detected


def is_pitch_button_trigger(body: StrategyRequest) -> bool:
    return body.trigger == _PITCH_TRIGGER


# Canned, instant replies for classify_message_intent()'s two
# short-circuit categories -- no LLM call, so a bare greeting or an
# off-topic message never reaches enrich_situation()/generate_strategy(),
# which would otherwise treat it as a real (if minimal) sales situation
# and fabricate a strategy around it (see _build_intent_shortcircuit()).
_GREETING_REPLY = (
    "Hi! I'm your AI Sales Director. Tell me about a prospect and what "
    "you're selling -- the company (and website if you have it), the "
    "product, and where things stand -- and I'll help you build a "
    "strategy, pitch, or outreach message."
)
_OFF_TOPIC_REPLY = (
    "I can only help with B2B sales strategy, pitches, and outreach "
    "messaging. Share a sales situation you're working on -- the "
    "prospect, what you're selling, and where things stand -- and I'll "
    "help you build a plan."
)
_INTENT_REPLIES = {"greeting": _GREETING_REPLY, "off_topic": _OFF_TOPIC_REPLY}


async def _persist_usage_logs(
    session: AsyncSession, conversation_id: UUID, message_id: UUID | None, user_id: UUID
) -> None:
    """Flushes whatever LLM calls were recorded (via usage_tracking.record_usage
    in app/services/llm.py) during the current request's usage_tracking_session()
    into llm_call_logs, tagged with this turn's conversation/message/user. A
    no-op when nothing was recorded (e.g. a canned reply with no LLM call)."""
    records = get_recorded_usage()
    if not records:
        return
    session.add_all(
        LlmCallLog(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
            provider=r.provider,
            model=r.model,
            call_name=r.call_name,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            total_tokens=r.total_tokens,
            cost_usd=r.cost_usd,
        )
        for r in records
    )
    await session.flush()


async def _build_intent_shortcircuit(
    session: AsyncSession,
    conversation: Conversation,
    conversation_id: UUID,
    body: StrategyRequest,
    situation_raw: str,
    intent: str,
) -> MessageResponse:
    """Persists the current turn (user message + a canned, instant
    assistant reply) for a "greeting" or "off_topic" classification from
    classify_message_intent(), and returns it -- run BEFORE the full
    pre-generation pipeline on any non-follow-up turn. No message_type,
    mirroring the pitch-flow's other canned filler messages (see
    _prepare_direct_pitch_intent()'s "please share the website and
    product" reply)."""
    user_message = Message(
        conversation_id=conversation_id,
        sender=MessageSender.user,
        content=(
            f"Website: {body.website_url or ''}\nProduct: {body.product or ''}\n"
            f"Situation: {situation_raw}"
        ),
    )
    session.add(user_message)

    reply_text = _INTENT_REPLIES[intent]
    message = Message(
        conversation_id=conversation_id,
        sender=MessageSender.assistant,
        content=reply_text,
        pipeline_metadata=None,
    )
    session.add(message)
    conversation.updated_at = datetime.now(timezone.utc)
    await session.flush()
    await _persist_usage_logs(session, conversation_id, message.id, conversation.user_id)
    await session.commit()
    return MessageResponse(
        id=message.id,
        sender=MessageSender.assistant,
        content=reply_text,
        created_at=message.created_at,
        sources=[],
        show_pitch_button=False,
    )


_RECENT_TURNS_LIMIT = 6  # 3 user + 3 assistant, matching _summarize_and_store_memory


async def _fetch_recent_turns(
    session: AsyncSession, conversation_id: UUID, limit: int = _RECENT_TURNS_LIMIT
) -> list[tuple[str, str]]:
    """Chronological (oldest-first) list of the most recent `limit` messages
    in the conversation, as (sender_value, content) pairs. Queries
    newest-first + limit (to actually capture the *latest* N turns in a
    long conversation, not the earliest N) then reverses back to
    chronological order -- callers must not skip that reversal. Shared by
    _summarize_and_store_memory (rolling summary) and the pitch/narrative
    generation call sites (raw turn-by-turn history for the LLM call)."""
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return [(m.sender.value, m.content) for m in reversed(result.scalars().all())]


def _turns_to_history_messages(turns: list[tuple[str, str]]) -> list[dict]:
    """Converts (sender, content) turns into the {"role", "content"} shape
    generate_narrative_strategy()/generate_pitch() thread into the LLM call
    as real conversation history, distinct from the rolling
    memory_summary/conversation_summary text blocks."""
    return [
        {
            "role": "user" if sender == MessageSender.user.value else "assistant",
            "content": content,
        }
        for sender, content in turns
    ]


async def _load_last_pitch_message_text(session: AsyncSession, conversation_id: UUID) -> str | None:
    """The text of the most recently generated sales_pitch_full pitch in
    this conversation, if any -- used by _prepare_pitch_context() to
    classify which opportunity type it used, so a "try another approach"
    regeneration can be told explicitly which one to avoid. Returns None
    if no pitch message exists yet (e.g. this is the first pitch turn)."""
    result = await session.execute(
        select(Message.content)
        .where(Message.conversation_id == conversation_id, Message.message_type == MessageType.pitch)
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _load_message_sources(session: AsyncSession, message_id: UUID) -> list[KnowledgeEntry]:
    """The KnowledgeEntry rows cited as sources on a given assistant
    message -- used to ground pitch generation in the same knowledge cards
    that backed the strategy turn it's following up on."""
    result = await session.execute(
        select(KnowledgeEntry)
        .join(MessageSource, MessageSource.knowledge_entry_id == KnowledgeEntry.id)
        .where(MessageSource.message_id == message_id)
    )
    return list(result.scalars().all())


async def _build_feedback_context(session: AsyncSession, conversation_id: UUID, limit: int = 3) -> str:
    """Most recent feedback on assistant messages in this conversation,
    formatted as background context (never a directive) for the next
    generation call."""
    result = await session.execute(
        select(Feedback, Message)
        .join(Message, Feedback.message_id == Message.id)
        .where(Message.conversation_id == conversation_id)
        .order_by(Feedback.created_at.desc())
        .limit(limit)
    )
    rows = result.all()
    if not rows:
        return ""
    lines = []
    for fb, _msg in rows:
        tag = "marked USEFUL" if fb.rating == FeedbackRating.useful else "marked NOT USEFUL"
        comment = f' — comment: "{fb.comment}"' if fb.comment else ""
        lines.append(f"- A prior response in this conversation was {tag}{comment}")
    return "\n".join(lines)


@dataclass
class _PreGenerationContext:
    website_url: str
    product: str
    situation_text: str
    enriched_situation: str
    pages: list[tuple[str, str]]
    company_snapshot_raw: str
    classification: dict
    methodology_hint: dict
    search_queries: list[str]
    retrieved: list
    is_followup: bool
    is_focused_followup: bool
    memory_context: str
    feedback_context: str


async def _prepare_turn(
    session: AsyncSession, conversation: Conversation, body: StrategyRequest
) -> tuple[bool, dict | None, str]:
    """Validates the request and determines follow-up status -- run before
    any DB write, matching the original endpoint's validate-before-insert
    order. Raises HTTPException(422) exactly as the endpoint always has for
    a missing situation on a non-follow-up turn."""
    website_url = body.website_url or ""
    product = body.product or ""
    # raw_message (the user's single combined chat message) carries the same
    # text as situation once the frontend's extraction step runs, but falls
    # back to it if situation was left empty for some other API consumer.
    situation_raw = (body.situation or body.raw_message or "").strip()

    prior_context = await _load_prior_pipeline_context(session, conversation.id)
    is_followup = _is_followup(situation_raw, website_url, product, prior_context)

    if not is_followup and not situation_raw:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Please describe the sales situation you'd like help with.",
                "missing": ["situation"],
            },
        )

    return is_followup, prior_context, situation_raw


_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$"
)


def _normalize_extracted_website(raw: str) -> str:
    """Validates and normalizes extract_website_url_from_text()'s raw
    output before it's ever treated as a real URL. Unlike the frontend's
    regex (which only ever matches an actual URL-shaped substring), this
    is a free-text LLM call with no format guarantee -- an occasional
    malformed/garbage response (extra words, punctuation, a hallucinated
    non-domain string) would otherwise reach urlparse()/
    socket.getaddrinfo() downstream in scraper.py and could raise a raw
    UnicodeError ("label empty or too long") that crashes the request
    instead of degrading gracefully to "no website found". Returns "" for
    anything that doesn't look like a plausible domain -- never partial
    garbage -- so a bad extraction is silently discarded rather than
    surfacing a confusing error for what is our own extraction mistake,
    not the user's input."""
    candidate = raw.strip().strip("\"'").rstrip("/")
    if not candidate:
        return ""
    host = candidate.split("://", 1)[-1].split("/", 1)[0]
    if not _DOMAIN_RE.match(host):
        return ""
    return candidate if "://" in candidate else f"https://{candidate}"


async def _run_pre_generation_pipeline(
    session: AsyncSession,
    conversation: Conversation,
    body: StrategyRequest,
    is_followup: bool,
    prior_context: dict | None,
    situation_raw: str,
) -> _PreGenerationContext:
    """Shared prefix of post_strategy() and post_strategy_stream(): the full
    enrich -> scrape/summarize -> classify -> detect methodology -> expand
    queries -> retrieve/rerank -> relevance-gate pipeline, short-circuited
    for follow-up turns, plus memory/feedback context building. Raises the
    same HTTPExceptions the pipeline always has (SSRF 400, relevance 422).
    Also short-circuits query expansion/retrieval/the relevance gate
    entirely for focused follow-ups (see _is_focused_followup) -- a short
    direct follow-up like "write the email for that" isn't asking for new
    sales guidance, and applies to both post_strategy and
    post_strategy_stream since they share this helper."""
    website_url = body.website_url or ""
    product = body.product or ""
    is_focused_followup = _is_focused_followup(situation_raw, is_followup)

    if is_followup:
        pages: list[tuple[str, str]] = []
        if not product:
            # A follow-up turn that doesn't retype the product shouldn't
            # lose the one already established for this prospect.
            product = (prior_context or {}).get("product") or ""
        prior_context = _merge_followup_situation(prior_context, situation_raw)
        company_snapshot_raw = prior_context["company_snapshot_raw"]
        classification = prior_context["classification"]
        methodology_hint = prior_context["methodology_hint"]
        enriched_situation = prior_context["enriched_situation"]
        if situation_raw and not _is_formatting_only_followup(situation_raw):
            # A substantive follow-up ("customer stopped responding") can
            # move the sales stage/problem type/persona -- reclassify
            # against the merged situation so downstream retrieval anchors
            # and the pitch prompt's stage-matching don't stay stuck on
            # turn-1's classification. A pure formatting ask ("make it
            # shorter") never changes the underlying situation, so it
            # skips this to avoid an unnecessary LLM call.
            candidate_types = await fetch_known_problem_types(session)
            classification = await classify_situation(enriched_situation, product, candidate_types)
            prior_context = {**prior_context, "classification": classification}
    else:
        if not product:
            # The chat composer only ever captures product via its own
            # manual field (see ChatComposer.tsx), never from the typed
            # message -- so a product mentioned inline in prose (e.g.
            # "the product i am selling is ...") would otherwise be
            # silently dropped to "" instead of reaching summarize_company/
            # classify_situation/the pitch prompt below.
            extraction_source = body.raw_message or situation_raw
            if extraction_source:
                product = await extract_product_from_text(extraction_source)

        # Rewrite the raw situation into a clean, professional description
        # before it feeds query expansion/rerank -- raw user text is often
        # too terse or informal to match knowledge-card titles well in
        # embedding space.
        enriched_situation = await enrich_situation(situation_raw, product)

        if not website_url:
            # Mirrors the product fallback above -- the frontend's own
            # website extraction is a regex requiring an http(s):// or
            # www. prefix (see ChatComposer.tsx) and only ever runs
            # client-side, so a bare domain ("smcworld.com") or a caller
            # that bypasses the frontend entirely would otherwise leave
            # this empty even when the URL is right there in the text.
            extraction_source = body.raw_message or situation_raw
            if extraction_source:
                raw_extracted_website = await extract_website_url_from_text(extraction_source)
                website_url = _normalize_extracted_website(raw_extracted_website)

        if website_url:
            try:
                pages = await fetch_company_pages(website_url)
            except UnsafeUrlError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="website_url must be a public http(s) URL",
                )
        else:
            pages = []
        company_snapshot_raw = await summarize_company(pages, product)

        candidate_types = await fetch_known_problem_types(session)
        classification = await classify_situation(situation_raw, product, candidate_types)
        methodology_hint = await detect_methodology(enriched_situation)

    if is_focused_followup:
        # Skip query expansion + retrieval entirely -- a short direct
        # follow-up isn't asking for new sales guidance, and running the
        # relevance gate against an empty retrieval result would
        # incorrectly 422 instead of just answering the follow-up.
        search_queries = []
        retrieved = []
    else:
        search_queries = await expand_queries(
            enriched_situation, product, classification, methodology_hint
        )
        anchor_parts = []
        if classification.get("problem_type"):
            anchor_parts.append(classification["problem_type"].replace("_", " "))
        if classification.get("sales_stage"):
            anchor_parts.append(classification["sales_stage"].replace("_", " "))
        if classification.get("buyer_persona"):
            anchor_parts.append(classification["buyer_persona"].replace("_", " "))
        if product:
            anchor_parts.append(product)
        search_queries.append(" ".join(anchor_parts))
        query_embeddings = embed_texts(search_queries)
        [rerank_embedding] = embed_texts([enriched_situation])
        retrieved = await _retrieve_cards(
            session, query_embeddings, rerank_embedding, top_n=_STRATEGY_TOP_N
        )

        best_distance = min((d for _, d in retrieved), default=None)
        if best_distance is None or best_distance > _NOT_RELEVANT_DISTANCE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": (
                        "I couldn't find strategy guidance that closely matches this yet. "
                        "Could you share a bit more about the situation — what's happening, "
                        "who's involved, or where things stand — so I can point you to the "
                        "most relevant advice?"
                    ),
                    "missing": [],
                },
            )

    memory_context = conversation.memory_summary or ""
    feedback_context = await _build_feedback_context(session, conversation.id)

    return _PreGenerationContext(
        website_url=website_url,
        product=product,
        situation_text=situation_raw,
        enriched_situation=enriched_situation,
        pages=pages,
        company_snapshot_raw=company_snapshot_raw,
        classification=classification,
        methodology_hint=methodology_hint,
        search_queries=search_queries,
        retrieved=retrieved,
        is_followup=is_followup,
        is_focused_followup=is_focused_followup,
        memory_context=memory_context,
        feedback_context=feedback_context,
    )


# The seller's own company -- static since this app only ever sells for
# MOTM. Paired with the per-request salesperson name (see
# _fetch_seller_name) as PITCH_GENERATION_PROMPT's SELLER IDENTITY block,
# so cold-call/email self-introductions have real values instead of
# falling back to a bracket placeholder.
_SELLER_COMPANY = "MOTM"


async def _fetch_seller_name(session: AsyncSession, user_id: UUID) -> str | None:
    """The salesperson's display name for pitch self-introductions.
    Profile.full_name is nullable (a user may not have set it), in which
    case the caller falls back to "(unknown)" -- never a placeholder."""
    profile = await session.get(Profile, user_id)
    return profile.full_name if profile else None


def _build_pitch_context(
    prior_context: dict,
    previous_strategy_content: str,
    output_format: str,
    conversation_summary: str,
    knowledge_entries: list[KnowledgeEntry],
    seller_name: str | None,
    latest_user_request: str = "",
    regeneration_directive: str = "",
) -> dict:
    """Assembles the context dict for generate_pitch(), filling every
    PITCH_GENERATION_PROMPT placeholder with a safe fallback so the prompt
    never hallucinates capabilities the website analysis didn't establish.

    output_format narrows PITCH_GENERATION_PROMPT's {sections_to_generate}
    to only the section(s) the user actually asked for (see
    detect_output_format() in app/services/llm.py). conversation_summary
    and knowledge_entries ground the pitch in what's actually been
    established in this conversation and in the same knowledge cards that
    backed the strategy turn it's following up on. seller_name/
    _SELLER_COMPANY give the model real values for self-introduction lines
    instead of a bracket placeholder -- previously none of these five were
    passed to generate_pitch() at all.

    company_name is read from company_snapshot_raw (via
    extract_company_name()), NOT from the classification dict --
    classify_situation()'s JSON schema has no company_name field, so
    reading it from `classification` here always silently produced the
    "not identified" fallback even when a website had been scraped and the
    real name was known (it just never reached the prompt via this field,
    only inside the separate website_summary block).

    latest_user_request is the CURRENT turn's raw typed message (e.g. "in
    more detail give me a sales pitch") -- everything else in this dict
    comes from prior_context, which is frozen at the FIRST turn that ran
    the full pipeline. Without this, a follow-up instruction like "more
    detail" or "focus on pricing" never reaches the model at all: it's
    used only to classify output_format via detect_output_format() and is
    otherwise discarded by callers (see _prepare_pitch_context()).

    regeneration_directive (built by _prepare_pitch_context() via
    classify_pitch_feedback()/classify_pitch_opportunity_type()) is a
    short, prominent block injected at the very top of
    PITCH_GENERATION_PROMPT -- empty string on an ordinary turn. See that
    function's docstring for why this exists as a separate, code-driven
    step rather than a prompt-only rule."""
    website_summary_raw = prior_context.get("company_snapshot_raw") or ""
    classification = prior_context.get("classification") or {}
    return {
        "seller_name": seller_name or "(unknown)",
        "seller_company": _SELLER_COMPANY,
        "company_name": extract_company_name(website_summary_raw) or "(unknown)",
        "product": prior_context.get("product") or "(not specified)",
        "situation": prior_context.get("enriched_situation") or "(not specified)",
        "persona": classification.get("buyer_persona", "unknown"),
        # classify_situation()'s output already computes this for the
        # strategy pipeline (generate_strategy()) -- previously never
        # threaded through to pitch generation/evaluation, so the W2R
        # "match the current sales stage" guidance had to be inferred by
        # the model from situation/previous_interaction text alone.
        "sales_stage": classification.get("sales_stage", "unknown"),
        "website_summary": (
            format_company_snapshot(website_summary_raw)
            if website_summary_raw
            else "(no website analysis available -- base the pitch on the "
            "product and situation only, do not claim specific company "
            "capabilities)"
        ),
        "previous_strategy": previous_strategy_content or "(no prior strategy on record)",
        "previous_interaction": prior_context.get("narrative") or previous_strategy_content or "",
        "sections_to_generate": _PITCH_SECTION_TEMPLATES.get(
            output_format, _PITCH_SECTION_TEMPLATES["all_formats"]
        ),
        "conversation_summary": conversation_summary or "(no earlier context yet)",
        "latest_request": latest_user_request.strip()
        or "(no new instruction -- same request as before)",
        "knowledge_context": (
            format_context(knowledge_entries)
            if knowledge_entries
            else "(no specific knowledge cards on record for this pitch -- "
            "ground the wording in the strategy and situation context above)"
        ),
        "regeneration_directive": regeneration_directive,
        # Filled by generate_verified_pitch() (llm.py) on its one retry
        # attempt when the first draft fails the W2R rubric -- "" here so
        # PITCH_GENERATION_PROMPT.format(**context) never KeyErrors on a
        # first attempt. See that prompt's module docstring.
        "compliance_feedback": "",
        # Not a PITCH_GENERATION_PROMPT placeholder -- str.format() ignores
        # unused kwargs, so this rides along in the same dict purely so
        # _finalize_pitch_nonstream/_pitch_stream_events can recover which
        # max_output_tokens budget generate_verified_pitch() should use
        # (see _PITCH_MAX_OUTPUT_TOKENS in llm.py) without a separate
        # parameter threaded through every call site.
        "output_format": output_format,
    }


def _build_pitch_message(conversation_id: UUID, pitch_text: str) -> Message:
    """pipeline_metadata=None is deliberate: _load_prior_pipeline_context
    filters on Message.pipeline_metadata.isnot(None), so a pitch turn is
    invisible to that lookup and a later strategy-pipeline follow-up (or a
    second pitch) still resolves to the last real strategy turn's context,
    not a pitch message that never carried classification/company_snapshot
    data. generate_pitch() also never produces the fields pipeline_metadata's
    other keys need, so leaving it None is the only correct choice here."""
    return Message(
        conversation_id=conversation_id,
        sender=MessageSender.assistant,
        content=pitch_text,
        message_type=MessageType.pitch,
        pipeline_metadata=None,
    )


async def _build_regeneration_directive(
    session: AsyncSession, conversation_id: UUID, situation_raw: str
) -> str:
    """Detects a "more detail" or "try another approach" follow-up in
    code (classify_pitch_feedback()) rather than leaving
    SALES_PITCH_MERGED_PROMPT to notice it itself -- a prompt-only rule
    for this, however specific and however many worked examples were
    added, proved unreliable in practice against real follow-ups: the
    model kept reusing the same opportunity type and paragraph length
    regardless. Returns a short block for {regeneration_directive} at the
    very top of PITCH_GENERATION_PROMPT (so it can't get lost among the
    ~900 lines of everything else), or "" when neither condition applies.

    Only meaningful for output formats that resolve to
    SALES_PITCH_MERGED_PROMPT (see _MERGED_PITCH_FORMATS in llm.py) --
    OPPORTUNITY POSITIONING's fixed type list and the 3-paragraph
    structure it refers to are specific to that prompt, so this is gated
    to those formats at the call site."""
    if not situation_raw.strip():
        return ""
    feedback_type = await classify_pitch_feedback(situation_raw)
    if feedback_type == "none":
        return ""

    if feedback_type == "more_detail":
        return (
            "==================================================\n"
            "THIS IS A \"MORE DETAIL\" FOLLOW-UP -- READ THIS FIRST\n"
            "==================================================\n"
            "The Sales Engineer's latest message is asking for MORE DETAIL "
            "on the pitch you already sent (visible in the conversation "
            "history). You MUST add a genuinely NEW paragraph introducing a "
            "second angle or technical detail not already in your previous "
            "pitch -- do NOT reword the same sentences at similar or "
            "greater length. See EXAMPLE 3 in the sales pitch instructions "
            "below for exactly what this does and does not look like.\n\n"
        )

    # feedback_type == "regenerate_different_angle"
    previous_pitch_text = await _load_last_pitch_message_text(session, conversation_id)
    if not previous_pitch_text:
        return ""
    previous_type = await classify_pitch_opportunity_type(previous_pitch_text)
    if previous_type == "unclear":
        return (
            "==================================================\n"
            "THIS IS A \"TRY ANOTHER APPROACH\" FOLLOW-UP -- READ THIS FIRST\n"
            "==================================================\n"
            "The Sales Engineer's latest message says the previous pitch "
            "(visible in conversation history) didn't work and wants a "
            "different approach. Use a genuinely different opportunity "
            "type and opening than your previous pitch. See EXAMPLE 4 in "
            "the sales pitch instructions below for exactly what this does "
            "and does not look like.\n\n"
        )
    remaining = ", ".join(
        label for key, label in OPPORTUNITY_TYPE_LABELS.items() if key != previous_type
    )
    return (
        "==================================================\n"
        "THIS IS A \"TRY ANOTHER APPROACH\" FOLLOW-UP -- READ THIS FIRST\n"
        "==================================================\n"
        "The Sales Engineer's latest message says the previous pitch "
        "(visible in conversation history) didn't work and wants a "
        "different approach. Your previous pitch used the "
        f"'{OPPORTUNITY_TYPE_LABELS[previous_type]}' angle. You MUST use a "
        f"genuinely different opportunity type this time -- pick from: "
        f"{remaining}. Do NOT use '{OPPORTUNITY_TYPE_LABELS[previous_type]}' "
        "or any close synonym of it again. See EXAMPLE 4 in the sales "
        "pitch instructions below for exactly what this does and does not "
        "look like.\n\n"
    )


async def _prepare_pitch_context(
    session: AsyncSession,
    conversation: Conversation,
    conversation_id: UUID,
    prior_context: dict,
    current_user: CurrentUser,
    output_format: str,
    conversation_history: list[dict] | None = None,
    situation_raw: str = "",
) -> tuple[dict, list[dict]]:
    """Builds generate_verified_pitch()'s context dict and
    conversation_history list -- the DB-querying prep work shared by the
    non-streaming pitch path (post_strategy) and the streaming pitch path
    (post_strategy_stream), before either one actually calls the LLM.

    conversation_history, if given, is used as-is instead of being
    fetched here -- callers that must add the current turn's user_message
    to the session BEFORE calling this (see _prepare_direct_pitch_intent)
    need to fetch history first, since AsyncSession autoflushes pending
    adds before any query and would otherwise leak the just-added message
    into its own "prior history".

    situation_raw is the CURRENT turn's raw typed message, threaded into
    _build_pitch_context() as latest_user_request -- see that function's
    docstring. The pitch-button trigger has no typed text for this turn,
    so its caller passes "" (see _prepare_pitch_trigger_context())."""
    last_strategy = await _load_last_strategy_message(session, conversation_id)
    knowledge_entries = (
        await _load_message_sources(session, last_strategy.id) if last_strategy else []
    )
    seller_name = await _fetch_seller_name(session, current_user.id)
    regeneration_directive = (
        await _build_regeneration_directive(session, conversation_id, situation_raw)
        if output_format in _MERGED_PITCH_FORMATS
        else ""
    )
    pitch_context = _build_pitch_context(
        prior_context,
        last_strategy.content if last_strategy else "",
        output_format,
        conversation.memory_summary or "",
        knowledge_entries,
        seller_name,
        situation_raw,
        regeneration_directive,
    )
    if conversation_history is None:
        recent_turns = await _fetch_recent_turns(session, conversation_id)
        conversation_history = _turns_to_history_messages(recent_turns)
    return pitch_context, conversation_history


async def _persist_pitch_message(
    session: AsyncSession,
    conversation: Conversation,
    conversation_id: UUID,
    pitch_text: str,
    output_format: str,
    eval_result: dict | None,
) -> Message:
    """eval_result is generate_verified_pitch()'s judge output for the
    winning attempt (or None if the judge itself never produced a usable
    result -- see _pitch_score() in llm.py) -- when present, the
    PitchEvaluation row is written in the same transaction as the message
    itself, synchronously, before the response is returned. No background
    task involved: the judge call already happened inside
    generate_verified_pitch(), before this function was even called."""
    pitch_message = _build_pitch_message(conversation_id, pitch_text)
    session.add(pitch_message)
    conversation.updated_at = datetime.now(timezone.utc)
    await session.flush()  # assigns pitch_message.id
    if eval_result is not None:
        session.add(
            PitchEvaluation(
                message_id=pitch_message.id,
                conversation_id=conversation_id,
                output_format=output_format,
                rubric_results=eval_result.get("rules", []),
                overall_score=eval_result["overall_score"],
                top_gaps=eval_result.get("top_gaps") or [],
            )
        )
    await _persist_usage_logs(session, conversation_id, pitch_message.id, conversation.user_id)
    await session.commit()
    return pitch_message


async def _finalize_pitch_nonstream(
    session: AsyncSession,
    conversation: Conversation,
    conversation_id: UUID,
    pitch_context: dict,
    conversation_history: list[dict],
) -> MessageResponse:
    """Blocking pitch generation + verification + persistence -- used by
    post_strategy() (no SSE involved there). post_strategy_stream()
    mirrors this via _pitch_stream_events, which also blocks on
    generate_verified_pitch() before emitting anything."""
    output_format = pitch_context.get("output_format", "all_formats")
    pitch_text, eval_result = await generate_verified_pitch(
        pitch_context,
        conversation_history=conversation_history,
        output_format=output_format,
    )
    pitch_message = await _persist_pitch_message(
        session, conversation, conversation_id, pitch_text, output_format, eval_result
    )

    return MessageResponse(
        id=pitch_message.id,
        sender=MessageSender.assistant,
        content=pitch_text,
        created_at=pitch_message.created_at,
        sources=[],
        show_pitch_button=False,
    )


async def _prepare_pitch_trigger_context(
    session: AsyncSession,
    conversation: Conversation,
    conversation_id: UUID,
    current_user: CurrentUser,
) -> tuple[dict, list[dict]]:
    """Pitch-button path context prep: 422s (asking for website/product)
    if none exists yet -- an explicit button click with no prior strategy
    turn is a client error. Shared by the non-streaming and streaming
    button-trigger paths."""
    prior_context = await _load_prior_pipeline_context(session, conversation_id)
    if prior_context is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Please provide the website and product first so I can "
                "generate a pitch.",
                "missing": ["website_url", "product"],
            },
        )
    return await _prepare_pitch_context(
        session, conversation, conversation_id, prior_context, current_user, "sales_pitch_full"
    )


async def _handle_pitch_trigger(
    session: AsyncSession,
    conversation: Conversation,
    conversation_id: UUID,
    current_user: CurrentUser,
) -> MessageResponse:
    """Non-streaming pitch-button path -- used by post_strategy()."""
    pitch_context, conversation_history = await _prepare_pitch_trigger_context(
        session, conversation, conversation_id, current_user
    )
    return await _finalize_pitch_nonstream(
        session, conversation, conversation_id, pitch_context, conversation_history
    )


async def _ask_for_missing_pitch_info(
    session: AsyncSession, conversation: Conversation, conversation_id: UUID, ask_text: str
) -> MessageResponse:
    """Persists and returns a canned clarifying-question reply -- no LLM
    call, no pipeline run -- shared by _prepare_direct_pitch_intent()'s two
    "not enough to build a pitch from yet" gates (nothing supplied at all,
    or a website/prior context with no resolvable product)."""
    message = Message(
        conversation_id=conversation_id,
        sender=MessageSender.assistant,
        content=ask_text,
        pipeline_metadata=None,
    )
    session.add(message)
    conversation.updated_at = datetime.now(timezone.utc)
    await session.flush()
    await _persist_usage_logs(session, conversation_id, message.id, conversation.user_id)
    await session.commit()
    return MessageResponse(
        id=message.id,
        sender=MessageSender.assistant,
        content=ask_text,
        created_at=message.created_at,
        sources=[],
        show_pitch_button=False,
    )


async def _prepare_direct_pitch_intent(
    session: AsyncSession,
    conversation: Conversation,
    conversation_id: UUID,
    body: StrategyRequest,
    situation_raw: str,
    prior_context: dict | None,
    current_user: CurrentUser,
    output_format: str,
) -> MessageResponse | tuple[dict, list[dict]]:
    """Capability-2 path: the user typed a pitch-intent message directly.

    Returns either a complete MessageResponse (the "please share website/
    product" ask -- an instant canned message, no LLM call, already
    persisted) or a (pitch_context, conversation_history) tuple ready to
    hand to generate_verified_pitch(). Callers check which
    with isinstance(): post_strategy() finalizes a tuple via
    _finalize_pitch_nonstream(), post_strategy_stream() finalizes it via
    _pitch_stream_events() -- see both callers below. output_format (from
    detect_output_format()) narrows the output to only the format(s)
    actually requested.

    has_fresh_context mirrors _is_followup()'s own rule elsewhere in this
    file ("if website_url or product: treat as a new full pass") -- a
    website_url/product on THIS request always means a new/different
    prospect, regardless of whether prior_context already exists from an
    earlier turn. Without this check, asking for a pitch on prospect B
    right after a pitch on prospect A (same conversation) would silently
    reuse prospect A's stale company/classification data, since
    prior_context being non-None alone doesn't mean it's still relevant.

    - If there's no prior_context AND no fresh website_url/product on this
      request, there is genuinely nothing to build a pitch from yet -- ask
      for it without calling the LLM (per spec). This filler message is
      not a pitch, so it is persisted without a message_type.
    - Otherwise, whenever prior_context is missing OR this request
      supplies fresh website_url/product, run the same pre-generation
      pipeline a normal strategy turn uses (scrape, classify, retrieve
      knowledge cards) to build fresh context, and persist it as a
      strategy-type message exactly like post_strategy()/
      post_strategy_stream() do (minus the narrative, since the user only
      asked for the pitch) -- so this turn's pitch is properly grounded in
      the CURRENT prospect, and later follow-ups resolve to real context.
    """
    # Extraction now runs on EVERY turn with text to read, not just turn
    # one -- a later turn naming a genuinely different product (e.g.
    # "Product: Plastic injection-molded cosmetic packaging\nGive me a
    # sales pitch.") must be noticed, not silently folded into the
    # existing thread via the ordinary follow-up branch below. This is
    # safe on ordinary follow-ups: PRODUCT_EXTRACTION_PROMPT/
    # WEBSITE_URL_EXTRACTION_PROMPT already return "" when nothing is
    # clearly stated, so a plain "in more detail" still extracts nothing.
    extraction_source = body.raw_message or situation_raw

    extracted_website = ""
    if not body.website_url and extraction_source:
        # A missing website degrades gracefully -- PITCH_GENERATION_PROMPT
        # has an explicit "(no website analysis available...)" fallback
        # for that case -- so this is a nice-to-have, not required for the
        # has-enough-to-proceed decision below. Mirrors
        # _run_pre_generation_pipeline()'s own fallback; done here too so
        # the "is this a new prospect" signal below can see it.
        raw_extracted_website = await extract_website_url_from_text(extraction_source)
        extracted_website = _normalize_extracted_website(raw_extracted_website)

    extracted_product = ""
    if not body.product and extraction_source:
        # Unlike website, there IS no equivalent fallback for a missing
        # product: SALES_PITCH_MERGED_PROMPT's 5R structure requires stating
        # what the product is, so generating with no real product just
        # invents one instead of asking -- this extraction attempt (plus
        # the prior_context fallback below) is what lets resolved_product
        # actually come up empty only when it genuinely should.
        extracted_product = await extract_product_from_text(extraction_source)

    # A product mentioned again is not the same signal as a DIFFERENT
    # product being named -- an ordinary follow-up can incidentally
    # restate the current product, and that must NOT force a full
    # re-scrape/reclassify. Compare against what's already on record.
    previous_product = (prior_context or {}).get("product") or ""
    incoming_product = body.product or extracted_product
    product_switched = (
        prior_context is not None
        and bool(incoming_product)
        and incoming_product.strip().lower() != previous_product.strip().lower()
    )

    # On turn one (nothing to compare against yet), any given info means
    # "fresh" -- same as before. On a later turn, only a genuinely new
    # website, or a genuinely DIFFERENT product, counts -- otherwise every
    # ordinary follow-up that happens to re-extract the current product
    # would look "fresh" and break the follow-up-reuse branch further
    # down (`elif situation_raw.strip():`).
    has_new_website_signal = bool(body.website_url or extracted_website)
    if prior_context is None:
        has_fresh_context = bool(
            body.website_url or extracted_website or body.product or extracted_product
        )
    else:
        has_fresh_context = has_new_website_signal or product_switched

    # Falls back to the prior turn's known product (same pattern as
    # _build_pitch_context()'s "product" field below) so a same-prospect
    # follow-up that only resends website_url doesn't lose it.
    resolved_product = incoming_product or previous_product
    # Falls back to the already-scraped site's URL ONLY when the product
    # hasn't changed (stored in prior_context by _build_pipeline_metadata())
    # -- a same-prospect follow-up that only resends the same product
    # shouldn't need the URL retyped, and summarize_company()'s own prompt
    # is product-aware (probable_buyer_personas/red_flags are computed
    # relative to the product being sold), so re-running against the same
    # site keeps that accurate. But when the product HAS changed and no
    # new website was given this turn, that's a genuinely different ask,
    # not "same prospect, new product" -- silently reusing the old site's
    # company_snapshot_raw here would attach an unrelated prospect's real
    # company details to a request that never mentioned them. Dropping to
    # "" here correctly falls through to _run_pre_generation_pipeline()'s
    # existing no-website path (skips fetch_company_pages(), and
    # PITCH_GENERATION_PROMPT already has a "(no website analysis
    # available...)" fallback for it) instead of fabricating relevance.
    effective_website_url = (
        body.website_url
        or extracted_website
        or ("" if product_switched else (prior_context or {}).get("website_url") or "")
    )

    # Fetched before the user_message below is added -- AsyncSession
    # autoflushes pending adds before any query, so fetching after would
    # leak this turn's own message into its own "prior history" (see
    # the matching note in post_strategy_stream() for the narrative path).
    recent_turns = await _fetch_recent_turns(session, conversation_id)
    conversation_history = _turns_to_history_messages(recent_turns)

    # Persisted here (not left to the strategy-only branch of
    # post_strategy()/post_strategy_stream()) because THAT branch is never
    # reached for a pitch-intent turn -- previously this meant a "give me
    # email for this" turn had no visible user message at all on reload,
    # only the assistant's reply. Mirrors the strategy branch's own format
    # for consistency across the conversation history view.
    user_message = Message(
        conversation_id=conversation_id,
        sender=MessageSender.user,
        content=f"Website: {body.website_url or ''}\nProduct: {body.product or ''}\nSituation: {situation_raw}",
    )
    session.add(user_message)

    if prior_context is None and not has_fresh_context:
        ask_text = (
            "I'd be glad to draft that for you -- could you first share the "
            "company website and the product you're selling so the pitch is "
            "specific to this prospect?"
        )
        return await _ask_for_missing_pitch_info(session, conversation, conversation_id, ask_text)

    if (prior_context is None or has_fresh_context) and not resolved_product:
        ask_text = (
            "Could you also share the product you're selling? I want the "
            "pitch to be grounded in what you actually offer rather than "
            "guessing."
        )
        return await _ask_for_missing_pitch_info(session, conversation, conversation_id, ask_text)

    if prior_context is None or has_fresh_context:
        fresh_body = body.model_copy(
            update={"product": resolved_product, "website_url": effective_website_url}
        )
        ctx = await _run_pre_generation_pipeline(
            session, conversation, fresh_body, is_followup=False, prior_context=None, situation_raw=situation_raw
        )
        if conversation.title is None:
            title_source = (situation_raw or ctx.enriched_situation or "").strip()
            if title_source:
                conversation.title = title_source[:60]

        prior_context = _build_pipeline_metadata(ctx, strategy_fields=None, narrative=None)
        # hidden=True: this is grounding context for later turns
        # (_load_last_strategy_message reads it back directly), not a
        # user-facing answer -- it's the enriched-situation rewrite, not a
        # pitch, and was previously rendering as its own chat bubble ahead
        # of the real generated pitch for the same turn.
        seed_message = Message(
            conversation_id=conversation_id,
            sender=MessageSender.assistant,
            content=ctx.enriched_situation,
            message_type=MessageType.strategy,
            pipeline_metadata=prior_context,
            hidden=True,
        )
        session.add(seed_message)
        conversation.updated_at = datetime.now(timezone.utc)
        await session.flush()  # assigns seed_message.id for the sources below

        sources = [
            MessageSource(
                message_id=seed_message.id,
                knowledge_entry_id=entry.id,
                relevance_score=entry_distance,
            )
            for entry, entry_distance in ctx.retrieved
        ]
        session.add_all(sources)
        await _persist_usage_logs(session, conversation_id, seed_message.id, conversation.user_id)
        await session.commit()
    elif situation_raw.strip():
        # Genuine follow-up pitch request (no fresh website/product): fold
        # the new text into the frozen turn-1 enriched_situation so it
        # becomes part of the primary SALES SITUATION context the model
        # sees, not just the {latest_request} side-note -- see
        # _merge_followup_situation()'s docstring for why this matters.
        # Without this, prior_context here is used completely unchanged
        # from whichever earlier turn first ran the full pipeline.
        prior_context = _merge_followup_situation(prior_context, situation_raw)
        if not _is_formatting_only_followup(situation_raw):
            candidate_types = await fetch_known_problem_types(session)
            classification = await classify_situation(
                prior_context["enriched_situation"],
                prior_context.get("product") or body.product or "",
                candidate_types,
            )
            prior_context = {**prior_context, "classification": classification}

    return await _prepare_pitch_context(
        session,
        conversation,
        conversation_id,
        prior_context,
        current_user,
        output_format,
        conversation_history,
        situation_raw,
    )


async def _handle_direct_pitch_intent(
    session: AsyncSession,
    conversation: Conversation,
    conversation_id: UUID,
    body: StrategyRequest,
    situation_raw: str,
    prior_context: dict | None,
    current_user: CurrentUser,
    output_format: str,
) -> MessageResponse:
    """Non-streaming direct-pitch-intent path -- used by post_strategy()."""
    prepared = await _prepare_direct_pitch_intent(
        session, conversation, conversation_id, body, situation_raw, prior_context, current_user, output_format
    )
    if isinstance(prepared, MessageResponse):
        return prepared
    pitch_context, conversation_history = prepared
    return await _finalize_pitch_nonstream(
        session, conversation, conversation_id, pitch_context, conversation_history
    )


async def _pitch_stream_events(
    session: AsyncSession,
    conversation: Conversation,
    conversation_id: UUID,
    pitch_context: dict,
    conversation_history: list[dict],
    background_tasks: BackgroundTasks,
):
    """SSE generator for pitch generation -- blocks on
    generate_verified_pitch() (generation + W2R judge, with one automatic
    regeneration attempt if the first draft fails the rubric) before
    emitting anything, then sends the winning pitch as a single
    narrative_chunk event (the same event name the frontend already
    listens for on the strategy path) followed by event: result / done.
    This is no longer true token-by-token streaming -- verifying the pitch
    before it's shown means the full text must exist before anything can
    be sent. See generate_verified_pitch()'s docstring (llm.py) for why
    this tradeoff was made deliberately."""

    def sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    output_format = pitch_context.get("output_format", "all_formats")
    try:
        pitch_text, eval_result = await generate_verified_pitch(
            pitch_context,
            conversation_history=conversation_history,
            output_format=output_format,
        )
    except Exception:
        logger.exception("Pitch generation failed for conversation %s", conversation_id)
        yield sse("error", {"message": "Pitch generation failed.", "kind": "server"})
        return

    pitch_message = await _persist_pitch_message(
        session, conversation, conversation_id, pitch_text, output_format, eval_result
    )
    background_tasks.add_task(_summarize_and_store_memory, conversation_id, pitch_message.id)

    yield sse("narrative_chunk", {"delta": pitch_text})
    response_payload = MessageResponse(
        id=pitch_message.id,
        sender=MessageSender.assistant,
        content=pitch_text,
        created_at=pitch_message.created_at,
        sources=[],
        show_pitch_button=False,
    )
    yield sse("result", response_payload.model_dump(mode="json"))
    yield sse("done", {})


async def _summarize_and_store_memory(conversation_id: UUID, trigger_message_id: UUID) -> None:
    """Background task, run after the response is sent. Opens its own
    session via async_session_factory -- the request-scoped session is
    closed by the time BackgroundTasks run. Best-effort: never surfaces
    errors to the user."""
    try:
        async with async_session_factory() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None:
                return
            recent = await _fetch_recent_turns(session, conversation_id)
            new_summary = await summarize_conversation_memory(conversation.memory_summary, recent)
            conversation.memory_summary = new_summary
            await session.commit()
    except Exception:
        logger.exception("Background memory summarization failed for conversation %s", conversation_id)
        return


def _build_strategy_response(
    assistant_message_id: UUID,
    ctx: _PreGenerationContext,
    strategy_fields: dict,
    show_pitch_button: bool = True,
) -> StrategyResponse:
    return StrategyResponse(
        id=assistant_message_id,
        company_snapshot=format_company_snapshot(ctx.company_snapshot_raw),
        situation_classification=SituationClassification(
            sales_stage=ctx.classification.get("sales_stage", "unknown"),
            problem_type=ctx.classification.get("problem_type", "unknown"),
            buyer_persona=ctx.classification.get("buyer_persona", "unknown"),
            objective=ctx.classification.get("objective", ""),
            missing_information=ctx.classification.get("missing_information", []),
        ),
        sources=[
            MessageSourceResponse(
                knowledge_entry_id=entry.id, title=entry.title, relevance_score=entry_distance
            )
            for entry, entry_distance in ctx.retrieved
        ],
        show_pitch_button=show_pitch_button,
        **strategy_fields,
    )


def _build_pipeline_metadata(
    ctx: _PreGenerationContext,
    strategy_fields: dict | None,
    narrative: str | None,
    company_match: dict | None = None,
) -> dict:
    return {
        "scraped_pages": [label for label, _ in ctx.pages],
        "company_snapshot_raw": ctx.company_snapshot_raw,
        "website_url": ctx.website_url,
        "product": ctx.product,
        "classification": ctx.classification,
        "enriched_situation": ctx.enriched_situation,
        "methodology_hint": ctx.methodology_hint,
        "search_queries": ctx.search_queries,
        "strategy": strategy_fields,
        "is_followup": ctx.is_followup,
        "is_focused_followup": ctx.is_focused_followup,
        "has_feedback_context": bool(ctx.feedback_context),
        "narrative": narrative,
        "company_match": company_match,
    }


def _build_message_response(
    message_id: UUID,
    ctx: _PreGenerationContext,
    content: str,
    created_at: datetime,
    narrative: str | None,
    strategy: dict | None = None,
    include_sources: bool = True,
    show_pitch_button: bool = True,
) -> MessageResponse:
    """Builds the MessageResponse payload for a narrative-only assistant
    turn -- used both by GET .../messages (reload) and the /strategy/stream
    SSE "result" event, so the same shape backs both the live and reloaded
    rendering of a message on the frontend (see HistoryMessageCard.tsx).
    include_sources=False for a company/situation mismatch refusal -- the
    retrieved cards weren't substantively used to produce it."""
    return MessageResponse(
        id=message_id,
        sender=MessageSender.assistant,
        content=content,
        created_at=created_at,
        situation_classification=SituationClassification(
            sales_stage=ctx.classification.get("sales_stage", "unknown"),
            problem_type=ctx.classification.get("problem_type", "unknown"),
            buyer_persona=ctx.classification.get("buyer_persona", "unknown"),
            objective=ctx.classification.get("objective", ""),
            missing_information=ctx.classification.get("missing_information", []),
        ),
        strategy=strategy,
        narrative=narrative,
        sources=(
            [
                MessageSourceResponse(
                    knowledge_entry_id=entry.id, title=entry.title, relevance_score=entry_distance
                )
                for entry, entry_distance in ctx.retrieved
            ]
            if include_sources
            else []
        ),
        show_pitch_button=show_pitch_button,
    )


@router.post(
    "/conversations/{conversation_id}/strategy",
    response_model=StrategyResponse | MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_strategy(
    conversation_id: UUID,
    body: StrategyRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(require_sales_engineer),
    session: AsyncSession = Depends(get_db_session),
) -> StrategyResponse | MessageResponse:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    start_usage_tracking()

    if is_pitch_button_trigger(body):
        return await _handle_pitch_trigger(session, conversation, conversation_id, current_user)

    is_followup, prior_context, situation_raw = await _prepare_turn(session, conversation, body)

    if not is_followup:
        intent = await classify_message_intent(situation_raw)
        if intent in _INTENT_REPLIES:
            return await _build_intent_shortcircuit(
                session, conversation, conversation_id, body, situation_raw, intent
            )

        # detect_output_format() must classify only the user's conversational
    # intent — not the full context block (website/product/situation) which
    # buries the pitch request and causes misclassification as strategy_only.
    # body.raw_message carries the user's typed message directly when the
    # frontend sends it separately; fall back to situation_raw only when
    # raw_message is absent (e.g. older API consumers).
    intent_text = (body.raw_message or situation_raw or "").strip()
    output_format = await resolve_output_format(
        session, conversation_id, intent_text, is_followup
    )
    if output_format != "strategy_only":
        return await _handle_direct_pitch_intent(
            session,
            conversation,
            conversation_id,
            body,
            situation_raw,
            prior_context,
            current_user,
            output_format,
        )

    user_message = Message(
        conversation_id=conversation_id,
        sender=MessageSender.user,
        content=(
            f"Website: {body.website_url or ''}\nProduct: {body.product or ''}\n"
            f"Situation: {body.situation or ''}"
        ),
    )
    session.add(user_message)

    ctx = await _run_pre_generation_pipeline(
        session, conversation, body, is_followup, prior_context, situation_raw
    )

    if conversation.title is None:
        title_source = (situation_raw or ctx.enriched_situation or "").strip()
        if title_source:
            conversation.title = title_source[:60]

    strategy_fields = await generate_strategy(
        company_snapshot=ctx.company_snapshot_raw,
        situation_classification=ctx.classification,
        product=ctx.product,
        situation=ctx.situation_text,
        context_entries=[entry for entry, _ in ctx.retrieved],
        conversation_memory=ctx.memory_context,
        feedback_context=ctx.feedback_context,
    )
    # generate_strategy() echoes these two back for its own prompt-building
    # purposes -- the router owns the response-facing (formatted) versions.
    strategy_fields.pop("company_snapshot")
    strategy_fields.pop("situation_classification")

    assistant_message = Message(
        conversation_id=conversation_id,
        sender=MessageSender.assistant,
        content=(
            f"{strategy_fields['situation_summary']}\n\n"
            f"Next action: {strategy_fields['next_action']}"
        ),
        message_type=MessageType.strategy,
        pipeline_metadata=_build_pipeline_metadata(ctx, strategy_fields, narrative=None),
    )
    session.add(assistant_message)
    conversation.updated_at = datetime.now(timezone.utc)
    await session.flush()  # assigns assistant_message.id for the sources below

    sources = [
        MessageSource(
            message_id=assistant_message.id,
            knowledge_entry_id=entry.id,
            relevance_score=entry_distance,
        )
        for entry, entry_distance in ctx.retrieved
    ]
    session.add_all(sources)
    await _persist_usage_logs(session, conversation_id, assistant_message.id, current_user.id)
    await session.commit()

    background_tasks.add_task(_summarize_and_store_memory, conversation_id, assistant_message.id)

    return _build_strategy_response(assistant_message.id, ctx, strategy_fields)


@router.post("/conversations/{conversation_id}/strategy/stream")
async def post_strategy_stream(
    conversation_id: UUID,
    body: StrategyRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(require_sales_engineer),
    session: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    """SSE version of post_strategy(): streams a narrative markdown version
    of the strategy token-by-token (event: narrative_chunk), then persists
    it and emits a MessageResponse-shaped payload (event: result), then
    event: done. This path does NOT also call generate_strategy() -- the
    narrative (with its mismatch guard, depth calibration, and anti-
    duplication rules) is the only response shown to the user here; the
    old structured-JSON card is a separate, no-longer-called-on-this-path
    representation (still used by the non-streaming post_strategy() below
    for other API consumers). Running both here previously produced two
    different-looking responses stacked in the UI for the same turn. All
    validation/404/422/SSRF-400 failures that post_strategy can raise
    happen here too, before the StreamingResponse is constructed -- only
    LLM failures during generation happen after headers are sent,
    reported as event: error."""
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    start_usage_tracking()

    def sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    async def _pitch_event_generator(response_payload: MessageResponse):
        """Only used for the instant ask_text canned reply (no LLM call,
        nothing to actually stream token-by-token) -- real pitch
        generation now streams via _pitch_stream_events instead."""
        yield sse("narrative_chunk", {"delta": response_payload.content})
        yield sse("result", response_payload.model_dump(mode="json"))
        yield sse("done", {})

    if is_pitch_button_trigger(body):
        pitch_context, conversation_history = await _prepare_pitch_trigger_context(
            session, conversation, conversation_id, current_user
        )
        return StreamingResponse(
            _pitch_stream_events(
                session,
                conversation,
                conversation_id,
                pitch_context,
                conversation_history,
                background_tasks,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    is_followup, prior_context, situation_raw = await _prepare_turn(session, conversation, body)

    if not is_followup:
        intent = await classify_message_intent(situation_raw)
        if intent in _INTENT_REPLIES:
            response_payload = await _build_intent_shortcircuit(
                session, conversation, conversation_id, body, situation_raw, intent
            )
            return StreamingResponse(
                _pitch_event_generator(response_payload),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

      # Same intent_text logic as post_strategy() -- classify only the user's
    # typed message, not the full context block.
    intent_text = (body.raw_message or situation_raw or "").strip()
    output_format = await resolve_output_format(
        session, conversation_id, intent_text, is_followup
    )
    if output_format != "strategy_only":
        prepared = await _prepare_direct_pitch_intent(
            session,
            conversation,
            conversation_id,
            body,
            situation_raw,
            prior_context,
            current_user,
            output_format,
        )
        if isinstance(prepared, MessageResponse):
            return StreamingResponse(
                _pitch_event_generator(prepared),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )
        pitch_context, conversation_history = prepared
        return StreamingResponse(
            _pitch_stream_events(
                session,
                conversation,
                conversation_id,
                pitch_context,
                conversation_history,
                background_tasks,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # Fetched before the current turn's user_message is added below --
    # AsyncSession autoflushes pending adds before a query executes, so
    # fetching after would let this turn's own message leak into its own
    # "prior history" context.
    recent_turns = await _fetch_recent_turns(session, conversation_id)
    conversation_history = _turns_to_history_messages(recent_turns)

    user_message = Message(
        conversation_id=conversation_id,
        sender=MessageSender.user,
        content=(
            f"Website: {body.website_url or ''}\nProduct: {body.product or ''}\n"
            f"Situation: {body.situation or ''}"
        ),
    )
    session.add(user_message)

    ctx = await _run_pre_generation_pipeline(
        session, conversation, body, is_followup, prior_context, situation_raw
    )

    if conversation.title is None:
        title_source = (situation_raw or ctx.enriched_situation or "").strip()
        if title_source:
            conversation.title = title_source[:60]

    async def event_generator():
        context_entries = [entry for entry, _ in ctx.retrieved]

        # Company/situation mismatch check runs as its own discrete step,
        # before generate_narrative_strategy() is ever called -- see
        # check_company_situation_match()'s docstring in llm.py for why
        # this moved out of the narrative prompt itself. Naturally skipped
        # on follow-up turns, since those never carry a new website_url.
        match_result: dict | None = None
        if ctx.website_url:
            match_result = await check_company_situation_match(
                company_context=format_company_snapshot(ctx.company_snapshot_raw),
                product=ctx.product,
                situation=ctx.situation_text,
                website_url=ctx.website_url,
            )
            if not match_result["match"]:
                refusal = (
                    f"The website analyzed ({ctx.website_url}) does not appear to match "
                    f"the sales context you described. {match_result['reason']} Please "
                    f"confirm this is the correct company website before I generate a "
                    f"recommendation. If the website is correct, you can also describe "
                    f"the company manually and I will work with that instead."
                )
                yield sse("narrative_chunk", {"delta": refusal})

                assistant_message = Message(
                    conversation_id=conversation_id,
                    sender=MessageSender.assistant,
                    content=refusal,
                    pipeline_metadata=_build_pipeline_metadata(
                        ctx, strategy_fields=None, narrative=refusal, company_match=match_result
                    ),
                )
                session.add(assistant_message)
                conversation.updated_at = datetime.now(timezone.utc)
                await session.flush()
                await _persist_usage_logs(session, conversation_id, assistant_message.id, conversation.user_id)
                await session.commit()

                background_tasks.add_task(
                    _summarize_and_store_memory, conversation_id, assistant_message.id
                )

                response_payload = _build_message_response(
                    assistant_message.id,
                    ctx,
                    refusal,
                    assistant_message.created_at,
                    narrative=refusal,
                    include_sources=False,
                    show_pitch_button=False,
                )
                yield sse("result", response_payload.model_dump(mode="json"))
                yield sse("done", {})
                return

        narrative_parts: list[str] = []
        try:
            async for delta in generate_narrative_strategy(
                company_snapshot=ctx.company_snapshot_raw,
                situation_classification=ctx.classification,
                product=ctx.product,
                situation=ctx.situation_text,
                context_entries=context_entries,
                conversation_memory=ctx.memory_context,
                feedback_context=ctx.feedback_context,
                focused_followup=ctx.is_focused_followup,
                enriched_situation=ctx.enriched_situation,
                conversation_history=conversation_history,
            ):
                narrative_parts.append(delta)
                yield sse("narrative_chunk", {"delta": delta})
        except Exception:
            logger.exception("Narrative generation failed for conversation %s", conversation_id)
            yield sse("error", {"message": "Narrative generation failed.", "kind": "server"})
            return

        full_narrative = "".join(narrative_parts)

        assistant_message = Message(
            conversation_id=conversation_id,
            sender=MessageSender.assistant,
            content=full_narrative,
            message_type=MessageType.strategy,
            pipeline_metadata=_build_pipeline_metadata(
                ctx,
                strategy_fields=None,
                narrative=full_narrative,
                company_match=match_result if ctx.website_url else None,
            ),
        )
        session.add(assistant_message)
        conversation.updated_at = datetime.now(timezone.utc)
        await session.flush()

        sources = [
            MessageSource(
                message_id=assistant_message.id,
                knowledge_entry_id=entry.id,
                relevance_score=entry_distance,
            )
            for entry, entry_distance in ctx.retrieved
        ]
        session.add_all(sources)
        await _persist_usage_logs(session, conversation_id, assistant_message.id, conversation.user_id)
        await session.commit()

        background_tasks.add_task(_summarize_and_store_memory, conversation_id, assistant_message.id)

        response_payload = _build_message_response(
            assistant_message.id,
            ctx,
            full_narrative,
            assistant_message.created_at,
            narrative=full_narrative,
            show_pitch_button=True,
        )
        yield sse("result", response_payload.model_dump(mode="json"))
        yield sse("done", {})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
