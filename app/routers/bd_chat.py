"""MOTM's own Business Development pipeline (persona=motm_bd).

This mirrors app/routers/chat.py's "SE" (Sales Engineer) pipeline closely,
but BD sells MOTM ITSELF rather than analyzing a prospect's website/product
as the object of the conversation -- see BDStrategyRequest's docstring in
app/models/schemas.py. Wherever a chat.py helper is persona-agnostic (it
only touches session/conversation/prior_context/current_user, never a
StrategyRequest-shaped body, and never hardcodes the SE knowledge-persona
filter), it's imported and reused directly rather than duplicated -- see
the long import block below. The handful of helpers that DO need to be
BD-specific are the ones that either read StrategyRequest-only fields
(website_url/product) that BDStrategyRequest doesn't have, or that run the
SE-only website-scrape-and-match-check step this pipeline must not run
unconditionally.

Reuse decision for the SE persona filter (see the task brief's point 2):
generalized app.routers.chat._retrieve_cards in place to accept a
`personas` parameter (default unchanged: sell_motm + shared) rather than
duplicating the whole multi-query retrieval/rerank function here -- it was
already generic aside from that one hardcoded filter, so generalizing it
was the minimal-duplication option and every existing SE call site keeps
its default (unchanged) behavior.

company_snapshot handling for BD (see the task brief's point 3): reused as
the PROSPECT's snapshot -- the company MOTM's BD team is selling TO, built
only when prospect_website is supplied and a scrape actually succeeds.
When no prospect_website is given, or the scrape fails/returns nothing,
this is "" (never the SE "Unknown"-JSON placeholder) and
generate_bd_strategy()/generate_bd_narrative_strategy() render that as
"(no prospect website supplied -- work from the situation description
only)" -- the pipeline is never blocked by a missing or failed lookup.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, Feedback, KnowledgeEntry, Message, MessageSource, PitchEvaluation
from app.db.session import get_db_session
from app.dependencies.auth import CurrentUser
from app.dependencies.roles import require_role
from app.models.schemas import (
    AppRole,
    BDHiringSignalRequest,
    BDHiringSignalResponse,
    BDStrategyRequest,
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
    SituationClassification,
    StrategyResponse,
)
from app.routers.chat import (
    _INTENT_REPLIES,
    _NOT_RELEVANT_DISTANCE,
    _STRATEGY_TOP_N,
    _PreGenerationContext,
    _ask_for_missing_pitch_info,
    _build_feedback_context,
    _build_message_response,
    _build_pipeline_metadata,
    _build_strategy_response,
    _fetch_recent_turns,
    _finalize_pitch_nonstream,
    _handle_pitch_trigger,
    _is_focused_followup,
    _is_followup,
    _is_formatting_only_followup,
    _load_prior_pipeline_context,
    _merge_followup_situation,
    _normalize_extracted_website,
    _persist_usage_logs,
    _pitch_stream_events,
    _prepare_pitch_context,
    _prepare_pitch_trigger_context,
    _retrieve_cards,
    _summarize_and_store_memory,
    _turns_to_history_messages,
    is_pitch_button_trigger,
    resolve_output_format,
)
from app.services.embeddings import embed_texts
from app.services.knowledge import fetch_known_problem_types
from app.services.llm import (
    classify_message_intent,
    classify_situation,
    detect_methodology,
    enrich_situation,
    expand_queries,
    extract_website_url_from_text,
    generate_bd_hiring_signal_analysis,
    generate_bd_hiring_signal_outreach,
    generate_bd_narrative_strategy,
    generate_bd_strategy,
    summarize_company,
)
from app.services.scraper import UnsafeUrlError, fetch_company_pages
from app.services.usage_tracking import start_usage_tracking

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bd-chat", tags=["bd-chat"])

require_motm_bd = require_role(AppRole.motm_bd)

# BD's knowledge-base scope: MOTM's own BD knowledge (positioning, pricing,
# ICP, objections, sales process, case studies) plus anything tagged
# "shared". Passed as the `personas` argument to the generalized
# app.routers.chat._retrieve_cards() -- see this module's docstring.
_BD_KNOWLEDGE_PERSONAS = (KnowledgePersona.motm_bd, KnowledgePersona.shared)

# Fixed "product" label threaded through the SE-pipeline helpers that take a
# `product` argument (enrich_situation, classify_situation, expand_queries,
# summarize_company) -- BD never sells a variable per-conversation product
# the way SE does, it always sells this one thing, so there is nothing for
# the user to supply here.
_BD_PRODUCT_LABEL = "MOTM's own B2B sourcing / manufacturing / vendor-development service"


async def _get_bd_conversation_or_404(
    session: AsyncSession, conversation_id: UUID, current_user: CurrentUser
) -> Conversation:
    """Isolation boundary: a motm_bd-role user must never be able to touch
    an SE (or any other persona's) conversation, even by guessing/reusing
    an id they hold from another role -- so this checks persona==motm_bd
    in addition to the ordinary user_id ownership check every endpoint
    below already needs."""
    conversation = await session.get(Conversation, conversation_id)
    if (
        conversation is None
        or conversation.user_id != current_user.id
        or conversation.persona != Persona.motm_bd
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    return conversation


@router.post(
    "/conversations",
    response_model=CreateConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_bd_conversation(
    current_user: CurrentUser = Depends(require_motm_bd),
    session: AsyncSession = Depends(get_db_session),
) -> CreateConversationResponse:
    # Mirrors chat.py's create_conversation() long-term-memory seed, but
    # scoped to persona=motm_bd so a BD conversation is never seeded from
    # (and never leaks) an SE conversation's memory_summary, even for a
    # user who holds both roles.
    prior_summary_result = await session.execute(
        select(Conversation.memory_summary)
        .where(
            Conversation.user_id == current_user.id,
            Conversation.persona == Persona.motm_bd,
            Conversation.memory_summary.isnot(None),
        )
        .order_by(Conversation.updated_at.desc())
        .limit(1)
    )
    seed_summary = prior_summary_result.scalar_one_or_none()

    conversation = Conversation(
        user_id=current_user.id, persona=Persona.motm_bd, memory_summary=seed_summary
    )
    session.add(conversation)
    await session.commit()

    return CreateConversationResponse(
        id=conversation.id,
        persona=conversation.persona,
        title=conversation.title,
        created_at=conversation.created_at,
    )


@router.get("/conversations", response_model=list[ConversationSummaryResponse])
async def list_bd_conversations(
    current_user: CurrentUser = Depends(require_motm_bd),
    session: AsyncSession = Depends(get_db_session),
) -> list[ConversationSummaryResponse]:
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.user_id == current_user.id,
            Conversation.persona == Persona.motm_bd,
        )
        .order_by(Conversation.updated_at.desc())
        .limit(50)
    )
    return [
        ConversationSummaryResponse(
            id=c.id, persona=c.persona, title=c.title, created_at=c.created_at, updated_at=c.updated_at
        )
        for c in result.scalars().all()
    ]


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummaryResponse)
async def rename_bd_conversation(
    conversation_id: UUID,
    body: ConversationRenameRequest,
    current_user: CurrentUser = Depends(require_motm_bd),
    session: AsyncSession = Depends(get_db_session),
) -> ConversationSummaryResponse:
    conversation = await _get_bd_conversation_or_404(session, conversation_id, current_user)
    conversation.title = body.title.strip()
    await session.commit()
    return ConversationSummaryResponse(
        id=conversation.id,
        persona=conversation.persona,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bd_conversation(
    conversation_id: UUID,
    current_user: CurrentUser = Depends(require_motm_bd),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    conversation = await _get_bd_conversation_or_404(session, conversation_id, current_user)
    await session.delete(conversation)
    await session.commit()


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationMessagesResponse,
)
async def get_bd_conversation_messages(
    conversation_id: UUID,
    current_user: CurrentUser = Depends(require_motm_bd),
    session: AsyncSession = Depends(get_db_session),
) -> ConversationMessagesResponse:
    conversation = await _get_bd_conversation_or_404(session, conversation_id, current_user)

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
                    knowledge_entry_id=entry.id, title=entry.title, relevance_score=source.relevance_score
                )
            )

    message_responses = []
    for m in messages:
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
                hiring_signal=metadata.get("hiring_signal"),
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
async def get_bd_pitch_evaluation(
    conversation_id: UUID,
    message_id: UUID,
    current_user: CurrentUser = Depends(require_motm_bd),
    session: AsyncSession = Depends(get_db_session),
) -> PitchEvaluationResponse:
    await _get_bd_conversation_or_404(session, conversation_id, current_user)

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
async def submit_bd_feedback(
    conversation_id: UUID,
    message_id: UUID,
    body: FeedbackRequest,
    current_user: CurrentUser = Depends(require_motm_bd),
    session: AsyncSession = Depends(get_db_session),
) -> FeedbackResponse:
    await _get_bd_conversation_or_404(session, conversation_id, current_user)

    message = await session.get(Message, message_id)
    if (
        message is None
        or message.conversation_id != conversation_id
        or message.sender != MessageSender.assistant
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    stmt = (
        pg_insert(Feedback)
        .values(
            message_id=message_id, user_id=current_user.id, rating=body.rating, comment=body.comment
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
        id=row.id, message_id=row.message_id, rating=row.rating, comment=row.comment, created_at=row.created_at
    )


# ---------------------------------------------------------------------------
# Strategy pipeline
# ---------------------------------------------------------------------------


async def _prepare_bd_turn(
    session: AsyncSession, conversation: Conversation, body: BDStrategyRequest
) -> tuple[bool, dict | None, str]:
    """BD counterpart of chat._prepare_turn(). Reuses chat._is_followup()
    as-is: that function's rule is "a website_url or product on THIS
    request always means a fresh pass" -- passing prospect_website/
    prospect_company into those two positional slots reproduces the
    identical rule for BD's fields without needing a new function."""
    situation_raw = (body.situation or body.raw_message or "").strip()

    prior_context = await _load_prior_pipeline_context(session, conversation.id)
    is_followup = _is_followup(
        situation_raw, body.prospect_website or "", body.prospect_company or "", prior_context
    )

    if not is_followup and not situation_raw:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Please describe the situation you'd like help with.",
                "missing": ["situation"],
            },
        )

    return is_followup, prior_context, situation_raw


async def _run_bd_pre_generation_pipeline(
    session: AsyncSession,
    conversation: Conversation,
    body: BDStrategyRequest,
    is_followup: bool,
    prior_context: dict | None,
    situation_raw: str,
) -> _PreGenerationContext:
    """BD counterpart of chat._run_pre_generation_pipeline(). Differs from
    the SE version in two deliberate ways (see this module's docstring and
    the task brief's point 2):

    1. The website lookup is OPTIONAL and never blocking -- only attempted
       when prospect_website was actually supplied, and any scrape failure
       (SSRF-rejected URL, network error, empty result) degrades to "no
       prospect snapshot" rather than raising. SE's 400 on UnsafeUrlError
       is deliberately NOT reproduced here.
    2. The company/situation match-check step (check_company_situation_match
       in llm.py) is never run at all -- its prompt encodes SE-specific
       rules (services-company non-match, job-shop/contract-manufacturer
       exceptions) that assume "does this company plausibly buy the
       manufactured component being sold", which has no equivalent meaning
       for "does this company plausibly want MOTM's BD services". Skipping
       it here, rather than forcing it to fit, is the deliberate choice.
    """
    prospect_website = body.prospect_website or ""
    is_focused_followup = _is_focused_followup(situation_raw, is_followup)
    pages: list[tuple[str, str]] = []

    if is_followup:
        prior_context = _merge_followup_situation(prior_context, situation_raw)
        company_snapshot_raw = prior_context["company_snapshot_raw"]
        classification = prior_context["classification"]
        methodology_hint = prior_context["methodology_hint"]
        enriched_situation = prior_context["enriched_situation"]
        if not prospect_website:
            # A follow-up that doesn't resupply the URL shouldn't lose the
            # one already established for this prospect.
            prospect_website = prior_context.get("website_url") or ""
        if situation_raw and not _is_formatting_only_followup(situation_raw):
            candidate_types = await fetch_known_problem_types(session)
            classification = await classify_situation(
                enriched_situation, _BD_PRODUCT_LABEL, candidate_types
            )
            prior_context = {**prior_context, "classification": classification}
    else:
        enriched_situation = await enrich_situation(situation_raw, _BD_PRODUCT_LABEL)

        if not prospect_website:
            extraction_source = body.raw_message or situation_raw
            if extraction_source:
                raw_extracted_website = await extract_website_url_from_text(extraction_source)
                prospect_website = _normalize_extracted_website(raw_extracted_website)

        if prospect_website:
            try:
                pages = await fetch_company_pages(prospect_website)
            except UnsafeUrlError:
                logger.warning(
                    "BD prospect_website failed SSRF validation, skipping scrape (non-blocking): %s",
                    prospect_website,
                )
                pages = []
            except Exception:
                logger.exception(
                    "BD website scrape failed for %s -- continuing without a prospect snapshot",
                    prospect_website,
                )
                pages = []

        # Unlike SE (which always calls summarize_company(), even with no
        # pages, to get the "Unknown"-JSON placeholder), BD only builds a
        # snapshot when a scrape actually produced something -- "" is the
        # deliberate "no prospect website supplied" sentinel consumed by
        # generate_bd_strategy()/generate_bd_narrative_strategy().
        company_snapshot_raw = await summarize_company(pages, _BD_PRODUCT_LABEL) if pages else ""

        candidate_types = await fetch_known_problem_types(session)
        classification = await classify_situation(situation_raw, _BD_PRODUCT_LABEL, candidate_types)
        methodology_hint = await detect_methodology(enriched_situation)

    if is_focused_followup:
        search_queries: list[str] = []
        retrieved = []
    else:
        search_queries = await expand_queries(
            enriched_situation, _BD_PRODUCT_LABEL, classification, methodology_hint
        )
        anchor_parts = []
        if classification.get("problem_type"):
            anchor_parts.append(classification["problem_type"].replace("_", " "))
        if classification.get("sales_stage"):
            anchor_parts.append(classification["sales_stage"].replace("_", " "))
        if classification.get("buyer_persona"):
            anchor_parts.append(classification["buyer_persona"].replace("_", " "))
        search_queries.append(" ".join(anchor_parts))
        query_embeddings = embed_texts(search_queries)
        [rerank_embedding] = embed_texts([enriched_situation])
        retrieved = await _retrieve_cards(
            session,
            query_embeddings,
            rerank_embedding,
            top_n=_STRATEGY_TOP_N,
            personas=_BD_KNOWLEDGE_PERSONAS,
        )

        best_distance = min((d for _, d in retrieved), default=None)
        if best_distance is None or best_distance > _NOT_RELEVANT_DISTANCE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": (
                        "I couldn't find BD guidance that closely matches this yet. "
                        "Could you share a bit more about the situation — what's "
                        "happening, who's involved, or where things stand — so I can "
                        "point you to the most relevant advice?"
                    ),
                    "missing": [],
                },
            )

    memory_context = conversation.memory_summary or ""
    feedback_context = await _build_feedback_context(session, conversation.id)

    return _PreGenerationContext(
        website_url=prospect_website,
        product=_BD_PRODUCT_LABEL,
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


def _bd_details_from_body(body: BDStrategyRequest) -> dict:
    """The optional situational fields BDStrategyRequest carries beyond
    situation/raw_message/trigger -- persisted into pipeline_metadata's
    "bd_details" key (JSONB) rather than new Message columns, per the task
    brief's constraint against new DB columns for these."""
    return {
        "prospect_company": body.prospect_company or "",
        "contact_designation": body.contact_designation or "",
        "opportunity_stage": body.opportunity_stage or "",
        "additional_context": body.additional_context or "",
    }


def _build_bd_pipeline_metadata(
    ctx: _PreGenerationContext,
    strategy_fields: dict | None,
    narrative: str | None,
    bd_details: dict,
) -> dict:
    """Wraps the reused chat._build_pipeline_metadata() (same key schema,
    so _load_prior_pipeline_context()/_merge_followup_situation()/
    _prepare_pitch_context() -- all reused from chat.py unchanged -- keep
    working against a BD-produced prior_context exactly as they do for
    SE), adding one extra "bd_details" key for the fields SE has no
    equivalent of. company_match is always None for BD -- see
    _run_bd_pre_generation_pipeline()'s docstring for why the match-check
    step never runs here."""
    metadata = _build_pipeline_metadata(ctx, strategy_fields, narrative, company_match=None)
    metadata["bd_details"] = bd_details
    return metadata


async def _build_bd_intent_shortcircuit(
    session: AsyncSession,
    conversation: Conversation,
    conversation_id: UUID,
    body: BDStrategyRequest,
    situation_raw: str,
    intent: str,
) -> MessageResponse:
    """BD counterpart of chat._build_intent_shortcircuit() -- identical
    behavior, just formats the persisted user-message content from
    BDStrategyRequest's fields instead of StrategyRequest's."""
    user_message = Message(
        conversation_id=conversation_id,
        sender=MessageSender.user,
        content=(
            f"Prospect: {body.prospect_company or ''}\nWebsite: {body.prospect_website or ''}\n"
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


async def _prepare_bd_direct_pitch_intent(
    session: AsyncSession,
    conversation: Conversation,
    conversation_id: UUID,
    body: BDStrategyRequest,
    situation_raw: str,
    prior_context: dict | None,
    current_user: CurrentUser,
    output_format: str,
) -> MessageResponse | tuple[dict, list[dict]]:
    """BD counterpart of chat._prepare_direct_pitch_intent(). Mirrors its
    structure closely; the one axis that doesn't translate 1:1 is SE's
    "product switched" fresh-context signal -- BD has no per-turn product
    to switch (it always sells the same thing), so the analogous signal
    here is the PROSPECT changing (prospect_company differs from the one
    recorded in prior_context's bd_details)."""
    extraction_source = body.raw_message or situation_raw

    extracted_website = ""
    if not body.prospect_website and extraction_source:
        raw_extracted_website = await extract_website_url_from_text(extraction_source)
        extracted_website = _normalize_extracted_website(raw_extracted_website)

    previous_prospect = ((prior_context or {}).get("bd_details") or {}).get("prospect_company") or ""
    incoming_prospect = body.prospect_company or ""
    prospect_switched = (
        prior_context is not None
        and bool(incoming_prospect)
        and incoming_prospect.strip().lower() != previous_prospect.strip().lower()
    )

    has_new_website_signal = bool(body.prospect_website or extracted_website)
    if prior_context is None:
        has_fresh_context = bool(body.prospect_website or extracted_website or body.prospect_company)
    else:
        has_fresh_context = has_new_website_signal or prospect_switched

    effective_website = (
        body.prospect_website
        or extracted_website
        or ("" if prospect_switched else (prior_context or {}).get("website_url") or "")
    )

    # Fetched before user_message is added below -- AsyncSession
    # autoflushes pending adds before any query, so fetching after would
    # leak this turn's own message into its own "prior history".
    recent_turns = await _fetch_recent_turns(session, conversation_id)
    conversation_history = _turns_to_history_messages(recent_turns)

    user_message = Message(
        conversation_id=conversation_id,
        sender=MessageSender.user,
        content=(
            f"Prospect: {body.prospect_company or ''}\nWebsite: {body.prospect_website or ''}\n"
            f"Situation: {situation_raw}"
        ),
    )
    session.add(user_message)

    if prior_context is None and not has_fresh_context and not situation_raw.strip():
        ask_text = (
            "I'd be glad to draft that -- could you first tell me a bit about "
            "the situation (and the prospect, if there is one) so the pitch is "
            "grounded in what's actually happening?"
        )
        return await _ask_for_missing_pitch_info(session, conversation, conversation_id, ask_text)

    if prior_context is None or has_fresh_context:
        fresh_body = body.model_copy(update={"prospect_website": effective_website})
        ctx = await _run_bd_pre_generation_pipeline(
            session, conversation, fresh_body, is_followup=False, prior_context=None, situation_raw=situation_raw
        )
        if conversation.title is None:
            title_source = (situation_raw or ctx.enriched_situation or "").strip()
            if title_source:
                conversation.title = title_source[:60]

        prior_context = _build_bd_pipeline_metadata(
            ctx, strategy_fields=None, narrative=None, bd_details=_bd_details_from_body(body)
        )
        # hidden=True: grounding context for later turns
        # (_load_last_strategy_message reads it back directly), not a
        # user-facing answer -- mirrors chat.py's identical seed message.
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
        await session.flush()

        sources = [
            MessageSource(
                message_id=seed_message.id, knowledge_entry_id=entry.id, relevance_score=entry_distance
            )
            for entry, entry_distance in ctx.retrieved
        ]
        session.add_all(sources)
        await _persist_usage_logs(session, conversation_id, seed_message.id, conversation.user_id)
        await session.commit()
    elif situation_raw.strip():
        prior_context = _merge_followup_situation(prior_context, situation_raw)
        if not _is_formatting_only_followup(situation_raw):
            candidate_types = await fetch_known_problem_types(session)
            classification = await classify_situation(
                prior_context["enriched_situation"], _BD_PRODUCT_LABEL, candidate_types
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


async def _handle_bd_direct_pitch_intent(
    session: AsyncSession,
    conversation: Conversation,
    conversation_id: UUID,
    body: BDStrategyRequest,
    situation_raw: str,
    prior_context: dict | None,
    current_user: CurrentUser,
    output_format: str,
) -> MessageResponse:
    prepared = await _prepare_bd_direct_pitch_intent(
        session, conversation, conversation_id, body, situation_raw, prior_context, current_user, output_format
    )
    if isinstance(prepared, MessageResponse):
        return prepared
    pitch_context, conversation_history = prepared
    return await _finalize_pitch_nonstream(
        session, conversation, conversation_id, pitch_context, conversation_history
    )


@router.post(
    "/conversations/{conversation_id}/strategy",
    response_model=StrategyResponse | MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_bd_strategy(
    conversation_id: UUID,
    body: BDStrategyRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(require_motm_bd),
    session: AsyncSession = Depends(get_db_session),
) -> StrategyResponse | MessageResponse:
    conversation = await _get_bd_conversation_or_404(session, conversation_id, current_user)
    start_usage_tracking()

    if is_pitch_button_trigger(body):
        return await _handle_pitch_trigger(session, conversation, conversation_id, current_user)

    is_followup, prior_context, situation_raw = await _prepare_bd_turn(session, conversation, body)

    if not is_followup:
        intent = await classify_message_intent(situation_raw)
        if intent in _INTENT_REPLIES:
            return await _build_bd_intent_shortcircuit(
                session, conversation, conversation_id, body, situation_raw, intent
            )

    intent_text = (body.raw_message or situation_raw or "").strip()
    output_format = await resolve_output_format(session, conversation_id, intent_text, is_followup)
    if output_format != "strategy_only":
        return await _handle_bd_direct_pitch_intent(
            session, conversation, conversation_id, body, situation_raw, prior_context, current_user, output_format
        )

    user_message = Message(
        conversation_id=conversation_id,
        sender=MessageSender.user,
        content=(
            f"Prospect: {body.prospect_company or ''}\nWebsite: {body.prospect_website or ''}\n"
            f"Situation: {body.situation or ''}"
        ),
    )
    session.add(user_message)

    ctx = await _run_bd_pre_generation_pipeline(session, conversation, body, is_followup, prior_context, situation_raw)

    if conversation.title is None:
        title_source = (situation_raw or ctx.enriched_situation or "").strip()
        if title_source:
            conversation.title = title_source[:60]

    strategy_fields = await generate_bd_strategy(
        prospect_snapshot=ctx.company_snapshot_raw,
        situation_classification=ctx.classification,
        situation=ctx.situation_text,
        context_entries=[entry for entry, _ in ctx.retrieved],
        conversation_memory=ctx.memory_context,
        feedback_context=ctx.feedback_context,
    )
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
        pipeline_metadata=_build_bd_pipeline_metadata(
            ctx, strategy_fields, narrative=None, bd_details=_bd_details_from_body(body)
        ),
    )
    session.add(assistant_message)
    conversation.updated_at = datetime.now(timezone.utc)
    await session.flush()

    sources = [
        MessageSource(
            message_id=assistant_message.id, knowledge_entry_id=entry.id, relevance_score=entry_distance
        )
        for entry, entry_distance in ctx.retrieved
    ]
    session.add_all(sources)
    await _persist_usage_logs(session, conversation_id, assistant_message.id, current_user.id)
    await session.commit()

    background_tasks.add_task(_summarize_and_store_memory, conversation_id, assistant_message.id)

    return _build_strategy_response(assistant_message.id, ctx, strategy_fields)


@router.post("/conversations/{conversation_id}/strategy/stream")
async def post_bd_strategy_stream(
    conversation_id: UUID,
    body: BDStrategyRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(require_motm_bd),
    session: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    """SSE version of post_bd_strategy() -- mirrors chat.py's
    post_strategy_stream() exactly, EXCEPT it never runs the company/
    situation match-check step (see _run_bd_pre_generation_pipeline()'s
    docstring for why) -- so there is no refusal-message branch here, only
    the narrative generation path."""
    conversation = await _get_bd_conversation_or_404(session, conversation_id, current_user)
    start_usage_tracking()

    def sse(event: str, data: dict) -> str:
        import json

        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    _sse_headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }

    async def _pitch_event_generator(response_payload: MessageResponse):
        yield sse("narrative_chunk", {"delta": response_payload.content})
        yield sse("result", response_payload.model_dump(mode="json"))
        yield sse("done", {})

    if is_pitch_button_trigger(body):
        pitch_context, conversation_history = await _prepare_pitch_trigger_context(
            session, conversation, conversation_id, current_user
        )
        return StreamingResponse(
            _pitch_stream_events(
                session, conversation, conversation_id, pitch_context, conversation_history, background_tasks
            ),
            media_type="text/event-stream",
            headers=_sse_headers,
        )

    is_followup, prior_context, situation_raw = await _prepare_bd_turn(session, conversation, body)

    if not is_followup:
        intent = await classify_message_intent(situation_raw)
        if intent in _INTENT_REPLIES:
            response_payload = await _build_bd_intent_shortcircuit(
                session, conversation, conversation_id, body, situation_raw, intent
            )
            return StreamingResponse(
                _pitch_event_generator(response_payload), media_type="text/event-stream", headers=_sse_headers
            )

    intent_text = (body.raw_message or situation_raw or "").strip()
    output_format = await resolve_output_format(session, conversation_id, intent_text, is_followup)
    if output_format != "strategy_only":
        prepared = await _prepare_bd_direct_pitch_intent(
            session, conversation, conversation_id, body, situation_raw, prior_context, current_user, output_format
        )
        if isinstance(prepared, MessageResponse):
            return StreamingResponse(
                _pitch_event_generator(prepared), media_type="text/event-stream", headers=_sse_headers
            )
        pitch_context, conversation_history = prepared
        return StreamingResponse(
            _pitch_stream_events(
                session, conversation, conversation_id, pitch_context, conversation_history, background_tasks
            ),
            media_type="text/event-stream",
            headers=_sse_headers,
        )

    recent_turns = await _fetch_recent_turns(session, conversation_id)
    conversation_history = _turns_to_history_messages(recent_turns)

    user_message = Message(
        conversation_id=conversation_id,
        sender=MessageSender.user,
        content=(
            f"Prospect: {body.prospect_company or ''}\nWebsite: {body.prospect_website or ''}\n"
            f"Situation: {body.situation or ''}"
        ),
    )
    session.add(user_message)

    ctx = await _run_bd_pre_generation_pipeline(session, conversation, body, is_followup, prior_context, situation_raw)

    if conversation.title is None:
        title_source = (situation_raw or ctx.enriched_situation or "").strip()
        if title_source:
            conversation.title = title_source[:60]

    async def event_generator():
        context_entries = [entry for entry, _ in ctx.retrieved]

        narrative_parts: list[str] = []
        try:
            async for delta in generate_bd_narrative_strategy(
                prospect_snapshot=ctx.company_snapshot_raw,
                situation_classification=ctx.classification,
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
            logger.exception("BD narrative generation failed for conversation %s", conversation_id)
            yield sse("error", {"message": "Narrative generation failed.", "kind": "server"})
            return

        full_narrative = "".join(narrative_parts)

        assistant_message = Message(
            conversation_id=conversation_id,
            sender=MessageSender.assistant,
            content=full_narrative,
            message_type=MessageType.strategy,
            pipeline_metadata=_build_bd_pipeline_metadata(
                ctx, strategy_fields=None, narrative=full_narrative, bd_details=_bd_details_from_body(body)
            ),
        )
        session.add(assistant_message)
        conversation.updated_at = datetime.now(timezone.utc)
        await session.flush()

        sources = [
            MessageSource(
                message_id=assistant_message.id, knowledge_entry_id=entry.id, relevance_score=entry_distance
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

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=_sse_headers)


# ---------------------------------------------------------------------------
# Hiring-Signal Outreach Agent
# ---------------------------------------------------------------------------
#
# A separate, fixed-shape workflow from the /strategy pipeline above: given
# a company that's publicly hiring a sales/BD/technical-sales-type role,
# infer the commercial objective behind that hire and produce a 3-message
# WhatsApp outreach sequence plus 4 canned replies (see
# app/services/prompts/bd_hiring_signal_prompt.py for the full spec this
# implements). Kept as its own endpoint rather than a `trigger` value on
# /strategy because the output (BDHiringSignalResponse) doesn't fit
# StrategyResponse's shape at all -- see the BD build plan's "Why a
# dedicated endpoint" note.
#
# Retrieval here intentionally skips classify_situation()/expand_queries():
# both are built around the sales-stage/problem-type taxonomy, which has no
# equivalent meaning for "what MOTM capabilities/differentiators are
# relevant to this company" -- a single embed-and-search on the supplied
# company/role/job-post text is a better fit and simpler.


@router.post(
    "/conversations/{conversation_id}/hiring-signal-outreach",
    response_model=BDHiringSignalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_bd_hiring_signal_outreach(
    conversation_id: UUID,
    body: BDHiringSignalRequest,
    current_user: CurrentUser = Depends(require_motm_bd),
    session: AsyncSession = Depends(get_db_session),
) -> BDHiringSignalResponse:
    conversation = await _get_bd_conversation_or_404(session, conversation_id, current_user)
    start_usage_tracking()

    usable = (body.company_name or body.company_website or body.job_post_text or "").strip()
    if not usable:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": (
                    "Please share the company name, website, or the job posting text so "
                    "I have something to work from."
                ),
                "missing": ["company_name_or_website_or_job_post"],
            },
        )

    query_text = " ".join(
        filter(
            None,
            [body.company_name, body.hiring_role, body.company_website, body.job_post_text, body.notes],
        )
    )
    [query_embedding] = embed_texts([query_text])
    retrieved = await _retrieve_cards(
        session,
        [query_embedding],
        query_embedding,
        top_n=_STRATEGY_TOP_N,
        personas=_BD_KNOWLEDGE_PERSONAS,
    )

    best_distance = min((d for _, d in retrieved), default=None)
    if best_distance is None or best_distance > _NOT_RELEVANT_DISTANCE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": (
                    "I couldn't find enough MOTM knowledge to ground an outreach message for "
                    "this yet. Could you add a bit more detail about the company or the role "
                    "they're hiring for?"
                ),
                "missing": [],
            },
        )

    user_message = Message(
        conversation_id=conversation_id,
        sender=MessageSender.user,
        content=(
            f"Company: {body.company_name or ''}\nWebsite: {body.company_website or ''}\n"
            f"Hiring role: {body.hiring_role or ''}\nJob post: {body.job_post_text or ''}"
        ),
    )
    session.add(user_message)

    if conversation.title is None:
        title_source = (body.company_name or body.hiring_role or "").strip()
        if title_source:
            conversation.title = title_source[:60]

    # Stage 1: analyze the hiring signal (no MOTM positioning/outreach yet
    # -- see BD_HIRING_SIGNAL_ANALYSIS_PROMPT's own STRICT RULES). Stage 2
    # takes this as its source of truth for the hiring role/commercial
    # interpretation rather than re-deriving it.
    signal_analysis = await generate_bd_hiring_signal_analysis(
        company_name=body.company_name or "",
        company_website=body.company_website or "",
        job_post_text=body.job_post_text or "",
        hiring_role=body.hiring_role or "",
        location=body.location or "",
        notes=body.notes or "",
    )

    result = await generate_bd_hiring_signal_outreach(
        signal_analysis=signal_analysis,
        company_name=body.company_name or "",
        company_website=body.company_website or "",
        job_post_text=body.job_post_text or "",
        hiring_role=body.hiring_role or "",
        location=body.location or "",
        contact_details=body.contact_details or "",
        sender_name=body.sender_name or "",
        notes=body.notes or "",
        context_entries=[entry for entry, _ in retrieved],
    )

    assistant_message = Message(
        conversation_id=conversation_id,
        sender=MessageSender.assistant,
        content=result.get("whatsapp_messages", {}).get("message_1", ""),
        message_type=MessageType.hiring_signal_outreach,
        pipeline_metadata={"hiring_signal": {**result, "signal_analysis": signal_analysis}},
    )
    session.add(assistant_message)
    conversation.updated_at = datetime.now(timezone.utc)
    await session.flush()

    sources = [
        MessageSource(message_id=assistant_message.id, knowledge_entry_id=entry.id, relevance_score=entry_distance)
        for entry, entry_distance in retrieved
    ]
    session.add_all(sources)
    await _persist_usage_logs(session, conversation_id, assistant_message.id, current_user.id)
    await session.commit()

    return BDHiringSignalResponse(
        id=assistant_message.id,
        signal_analysis=signal_analysis,
        sources=[
            MessageSourceResponse(knowledge_entry_id=entry.id, title=entry.title, relevance_score=entry_distance)
            for entry, entry_distance in retrieved
        ],
        **result,
    )
