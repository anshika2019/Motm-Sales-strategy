import json
import logging
import re
from collections.abc import AsyncIterator

from google import genai
from google.genai import errors, types
from openai import APIError as OpenAIAPIError
from openai import AsyncOpenAI

from app.config import settings
from app.db.models import KnowledgeEntry
from app.services.usage_tracking import record_usage
from app.services.prompts import (
    BD_HIRING_SIGNAL_ANALYSIS_PROMPT,
    BD_HIRING_SIGNAL_OUTREACH_PROMPT,
    BD_STRATEGY_NARRATIVE_PROMPT,
    COLD_CALL_SECTION_TEMPLATE,
    CONVERSATION_MEMORY_PROMPT,
    EMAIL_SECTION_TEMPLATE,
    FOLLOWUP_RESPONSE_PROMPT,
    MESSAGE_INTENT_PROMPT,
    METHODOLOGY_DETECTION_PROMPT,
    OPPORTUNITY_TYPE_CLASSIFICATION_PROMPT,
    OUTPUT_FORMAT_DETECTION_PROMPT,
    PITCH_EVALUATION_PROMPT,
    PITCH_FEEDBACK_CLASSIFICATION_PROMPT,
    PITCH_GENERATION_PROMPT,
    PRODUCT_EXTRACTION_PROMPT,
    QUERY_EXPANSION_PROMPT,
    SALES_PITCH_MERGED_PROMPT,
    # RETIRED — replaced by SALES_PITCH_MERGED_PROMPT
    # Kept here temporarily for reference. Safe to delete after testing.
    # SALES_PITCH_PROSE_TEMPLATE,
    SALES_PITCH_SUBSECTIONS,
    SITUATION_ENRICHMENT_PROMPT,
    STRATEGY_NARRATIVE_PROMPT,
    WEBSITE_URL_EXTRACTION_PROMPT,
    WHATSAPP_SECTION_TEMPLATE,
    _SALES_PITCH_GOLDEN_RULE_FOOTER,
)

from app.services.prompts.sales_pitch_meeting_script_prompt import SALES_PITCH_MEETING_SCRIPT_PROMPT
from app.services.prompts.sales_pitch_reengagement_prompt import SALES_PITCH_REENGAGEMENT_PROMPT

# gemini-2.5-flash returned 404 "no longer available to new users" on this
# account (confirmed live against the real API, not a docs assumption).
# gemini-3.6-flash (the replacement Gemini's own error message named) was
# confirmed working, but its free tier caps at 20 requests/day on this
# account -- nowhere near enough (one conversation turn alone makes 6-8+
# calls). gemini-2.5-flash-lite is ALSO blocked (404, same generation as
# 2.5-flash), but gemini-3.1-flash-lite is available and empirically has
# meaningfully higher free-tier headroom -- Google no longer publishes
# exact free-tier RPD numbers in static docs (moved to the authenticated
# AI Studio dashboard), so this was chosen by directly testing model
# availability/quota rather than from a docs table. Confirmed compatible
# with the same JSON mode / streaming / thinking_level config as the
# flash model. Trade-off: a lite model is lower quality than flash --
# revisit once billing is upgraded or the flash quota is no longer the
# blocker. Check ai.google.dev/gemini-api/docs/models and
# aistudio.google.com/rate-limit for current availability/quota.
_MODEL_GEMINI = "gemini-3.1-flash-lite"

# Used for the quality-critical, user-visible generation calls (final
# strategy/pitch text, company summarization, the company/situation match
# gate) -- routed here instead of Gemini because these are the calls whose
# output the Sales Engineer reads/sends directly, and where reasoning
# quality most affects correctness (see the routing rationale in the
# implementation plan this migration was made from). Short classification/
# extraction calls stay on Gemini for cost.
_MODEL_OPENAI = "gpt-4.1-mini"

_client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
_openai_client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
_logger = logging.getLogger(__name__)

# Thinking level -- this model generation's equivalent of Groq's
# reasoning_effort. Confirmed via direct testing that thinking_budget=0
# (the token-count-based control used on older Gemini models) is REJECTED
# by this model (400 INVALID_ARGUMENT) -- it cannot fully disable
# thinking -- so thinking_level (an enum: MINIMAL/LOW/MEDIUM/HIGH) is used
# instead, which this model does accept. MINIMAL is for narrow
# classification/extraction calls where the task is closer to structured
# pattern-matching than reasoning; MEDIUM is for the generation calls
# where output quality benefits from more deliberation (strategy, pitch
# copy) -- matches the "medium" Groq reasoning_effort these calls were
# already tuned to before this migration.
_THINKING_MINIMAL = types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL)
_THINKING_MEDIUM = types.ThinkingConfig(thinking_level=types.ThinkingLevel.MEDIUM)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CARD_CONTENT_CHAR_LIMIT = 700


def _format_context(context_entries: list[KnowledgeEntry]) -> str:
    # Truncated per card: this context is duplicated in full into both
    # generate_strategy() and generate_narrative_strategy()'s prompts, and
    # keeping cards short keeps prompt size (and therefore cost/latency)
    # bounded even when _STRATEGY_TOP_N cards are retrieved.
    return "\n\n".join(
        f"[{i + 1}] {entry.title}\n{entry.content[:_CARD_CONTENT_CHAR_LIMIT]}"
        for i, entry in enumerate(context_entries)
    )


def _record_gemini_usage(response, call_name: str) -> None:
    """Shared by every Gemini generate_content() call site below --
    usage_metadata is None only in the (rare) case the API returned no
    usage data at all, e.g. an error response that still parsed."""
    usage = response.usage_metadata
    if usage is not None:
        record_usage(
            "gemini", _MODEL_GEMINI, call_name,
            usage.prompt_token_count or 0, usage.candidates_token_count or 0,
        )


def _extract_json(raw: str) -> dict:
    """
    Gemini's JSON mode (response_mime_type="application/json") is reliable
    but the response can still occasionally include stray whitespace or
    (rarely) wrapping text. Fall back to pulling the first {...} block out
    of the response rather than raising, since a formatting slip should
    not 500 the request.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _format_company_snapshot(snapshot_raw: str) -> str:
    """
    The company summarizer returns a JSON string.
    Parse it and reformat it as clean readable text
    so the strategy prompt can use it easily.
    If parsing fails, return the raw string as fallback.
    """
    try:
        data = json.loads(snapshot_raw)
        lines = []

        if data.get("company_name"):
            lines.append(f"Company: {data['company_name']}")
        if data.get("what_they_do"):
            lines.append(f"What they do: {data['what_they_do']}")
        if data.get("products_or_services"):
            lines.append(f"Products/Services: {', '.join(data['products_or_services'])}")
        if data.get("industries_they_serve"):
            lines.append(f"Industries: {', '.join(data['industries_they_serve'])}")
        if data.get("manufacturing_capabilities"):
            lines.append(f"Capabilities: {', '.join(data['manufacturing_capabilities'])}")
        if data.get("certifications"):
            lines.append(f"Certifications: {data['certifications']}")
        if data.get("company_size_signals"):
            lines.append(f"Size signals: {data['company_size_signals']}")
        if data.get("probable_buyer_personas"):
            lines.append(f"Likely buyer personas: {', '.join(data['probable_buyer_personas'])}")

        facts = data.get("facts_from_website", [])
        if facts:
            lines.append("Facts from website:")
            for f in facts:
                lines.append(f"  - {f}")

        hypotheses = data.get("sales_hypotheses", [])
        if hypotheses:
            lines.append(
                "UNVERIFIED HYPOTHESES (inferred guesses, NOT facts -- do not "
                "state these as confirmed in any output; treat exactly like any "
                "other unconfirmed prospect/product connection):"
            )
            for h in hypotheses:
                lines.append(f"  - {h}")

        red_flags = data.get("red_flags", [])
        if red_flags:
            lines.append("Red flags:")
            for r in red_flags:
                lines.append(f"  - {r}")

        return "\n".join(lines)

    except (json.JSONDecodeError, TypeError):
        # snapshot was not valid JSON — return as-is
        return snapshot_raw


def extract_company_name(snapshot_raw: str) -> str | None:
    """Pulls just the company_name field out of summarize_company()'s raw
    JSON -- used by _build_pitch_context() in chat.py to get the prospect's
    actual company name for PITCH_GENERATION_PROMPT. "Unknown" is
    summarize_company()'s own fallback value when no pages were scraped at
    all, so it's treated the same as missing/blank here. Returns None
    (never a placeholder string) when no real name is available -- callers
    decide what to substitute."""
    try:
        data = json.loads(snapshot_raw)
    except (json.JSONDecodeError, TypeError):
        return None
    name = (data.get("company_name") or "").strip()
    if not name or name.lower() == "unknown":
        return None
    return name


def _format_memory_and_feedback_blocks(
    conversation_memory: str, feedback_context: str
) -> tuple[str, str]:
    """Builds the two optional prompt sections shared by generate_strategy()
    and generate_narrative_strategy(). Both are framed as background
    context to calibrate tone/consistency, never as a directive that
    overrides the retrieved knowledge cards -- and are omitted entirely
    when empty so early-conversation turns don't dilute the prompt."""
    memory_block = ""
    if conversation_memory.strip():
        memory_block = f"""
CONVERSATION MEMORY (background context only -- do not restate verbatim,
use it to stay consistent with what's already known about this prospect):
{conversation_memory.strip()}
"""
    feedback_block = ""
    if feedback_context.strip():
        feedback_block = f"""
PRIOR FEEDBACK IN THIS CONVERSATION (context only, not a directive -- use
it to calibrate tone/depth, do not reference it explicitly):
{feedback_context.strip()}
"""
    return memory_block, feedback_block


def format_context(context_entries: list[KnowledgeEntry]) -> str:
    """Public wrapper around _format_context -- lets callers outside this
    module (e.g. chat.py building the pitch-generation knowledge_context)
    format retrieved cards the same way generate_strategy()/
    generate_narrative_strategy() do, without duplicating the numbering
    logic."""
    return _format_context(context_entries)


def format_company_snapshot(snapshot_raw: str) -> str:
    """Public wrapper -- summarize_company() returns raw JSON meant for
    generate_strategy()'s prompt; callers that want the human-readable
    rendering (e.g. the API response) use this instead."""
    return _format_company_snapshot(snapshot_raw)


def _to_gemini_contents(
    conversation_history: list[dict] | None, final_text: str
) -> list[types.Content]:
    """Converts the app's internal {"role": "user"|"assistant", "content":
    str} turn format (see app/routers/chat.py's _turns_to_history_messages)
    into Gemini's Content list, appending final_text as the last user turn.
    Gemini uses role "model" where the app's internal format -- and Groq's
    OpenAI-compatible API before it -- used "assistant"."""
    contents = [
        types.Content(
            role="model" if turn["role"] == "assistant" else "user",
            parts=[types.Part.from_text(text=turn["content"])],
        )
        for turn in (conversation_history or [])
    ]
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=final_text)]))
    return contents


class _TextResponse:
    """Thin shim so _openai_generate()/_gemini_generate() can expose the
    same `.text` attribute the raw Gemini generate_content() responses
    already have, without touching any downstream _extract_json()/.strip()
    logic regardless of which provider actually served a given call."""

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


def _is_openai_quota_exhausted(exc: Exception) -> bool:
    """True when an OpenAI call failed specifically because the account is
    out of credits/quota (billing), as opposed to a transient rate limit
    (too many requests/sec, which should still retry against OpenAI, not
    silently reroute to a different model with different output quality).

    Confirmed live against this account's actual exhausted-credits response:
    a 429 RateLimitError whose body is
    {"error": {"code": "credit_balance_exhausted", "type": "insufficient_quota", ...}}
    -- note `code` and `type` don't agree on which word they use, so both
    are checked (case-insensitively, substring match -- OpenAI does not
    document these as a closed enum). A message-substring fallback is kept
    in case neither field is populated for some other exhaustion variant."""
    if not isinstance(exc, OpenAIAPIError):
        return False
    haystack = " ".join(
        str(part).lower() for part in (exc.code, exc.type, str(exc)) if part
    )
    return "quota" in haystack or "credit" in haystack


async def _openai_generate(
    *,
    call_name: str,
    system_instruction: str | None,
    user_content: str,
    json_mode: bool = False,
    max_output_tokens: int | None = None,
) -> _TextResponse:
    """Single-turn (no conversation_history) OpenAI call, used by the
    three switched functions that don't need multi-turn history:
    summarize_company(), check_company_situation_match(),
    generate_strategy(). See _to_openai_messages() for the history-aware
    counterpart used by generate_pitch()/generate_narrative_strategy().

    call_name identifies the caller for usage_tracking's per-call cost log
    (this helper is shared by multiple functions, so the model/provider
    alone wouldn't distinguish them)."""
    messages: list[dict] = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": user_content})

    kwargs: dict = {"model": _MODEL_OPENAI, "messages": messages}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if max_output_tokens is not None:
        kwargs["max_tokens"] = max_output_tokens

    response = await _openai_client.chat.completions.create(**kwargs)
    if response.usage:
        record_usage(
            "openai", _MODEL_OPENAI, call_name,
            response.usage.prompt_tokens, response.usage.completion_tokens,
        )
    return _TextResponse(response.choices[0].message.content or "")


async def _gemini_generate(
    *,
    call_name: str,
    system_instruction: str | None,
    user_content: str,
    json_mode: bool = False,
    max_output_tokens: int | None = None,
) -> _TextResponse:
    """Gemini equivalent of _openai_generate(), same signature/return shape
    -- used as the fallback when an OpenAI call fails with
    _is_openai_quota_exhausted(), so callers can swap providers without any
    other code changing (json_mode -> response_mime_type, max_output_tokens
    passes straight through)."""
    config_kwargs: dict = {"thinking_config": _THINKING_MINIMAL}
    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"
    if max_output_tokens is not None:
        config_kwargs["max_output_tokens"] = max_output_tokens

    response = await _client.aio.models.generate_content(
        model=_MODEL_GEMINI,
        contents=user_content,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    _record_gemini_usage(response, call_name)
    return _TextResponse(response.text or "")


def _to_openai_messages(
    conversation_history: list[dict] | None,
    final_text: str,
    system_instruction: str | None = None,
) -> list[dict]:
    """OpenAI counterpart to _to_gemini_contents(). Simpler than the
    Gemini version since the app's internal "user"/"assistant" role
    vocabulary already matches OpenAI's -- no "assistant"->"model"
    remapping needed."""
    messages: list[dict] = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.extend(
        {"role": turn["role"], "content": turn["content"]}
        for turn in (conversation_history or [])
    )
    messages.append({"role": "user", "content": final_text})
    return messages


async def _openai_generate_stream(
    *,
    call_name: str,
    messages: list[dict],
    max_output_tokens: int | None = None,
    temperature: float | None = None,
) -> AsyncIterator[str]:
    """Streaming counterpart to _openai_generate(), used by
    generate_narrative_strategy(). Yields plain text deltas, matching the
    async-generator contract its SSE caller in app/routers/chat.py already
    expects from the Gemini path.

    temperature defaults to None (provider default) so it only affects
    callers that opt in -- see _PITCH_TEMPERATURE in generate_pitch().

    call_name identifies the caller for usage_tracking, same as
    _openai_generate(). stream_options={"include_usage": True} makes the
    API send one extra final chunk with empty choices and a populated
    .usage -- that's where token counts come from for a streamed call."""
    kwargs: dict = {
        "model": _MODEL_OPENAI,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if max_output_tokens is not None:
        kwargs["max_tokens"] = max_output_tokens
    if temperature is not None:
        kwargs["temperature"] = temperature

    stream = await _openai_client.chat.completions.create(**kwargs)
    async for chunk in stream:
        if chunk.usage:
            record_usage(
                "openai", _MODEL_OPENAI, call_name,
                chunk.usage.prompt_tokens, chunk.usage.completion_tokens,
            )
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


def _messages_to_gemini(messages: list[dict]) -> tuple[str | None, list[types.Content]]:
    """Converts an OpenAI-style messages list ({"role": "system"|"user"|
    "assistant", "content": str}, as built by _to_openai_messages()) into a
    Gemini system_instruction string plus a Content list -- the format
    _gemini_generate_stream() needs to serve as a drop-in fallback for
    _openai_generate_stream() callers. At most one "system" message is
    expected (that's all _to_openai_messages() ever produces); if present
    it's pulled out rather than turned into a Content entry, since Gemini
    takes the system prompt as a separate config field, not part of
    `contents`."""
    system_instruction: str | None = None
    contents: list[types.Content] = []
    for message in messages:
        if message["role"] == "system":
            system_instruction = message["content"]
            continue
        role = "model" if message["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=message["content"])]))
    return system_instruction, contents


async def _gemini_generate_from_messages(
    *,
    call_name: str,
    messages: list[dict],
    max_output_tokens: int | None = None,
    temperature: float | None = None,
) -> _TextResponse:
    """Non-streaming counterpart to _gemini_generate_stream() -- same
    messages-list input (built by _to_openai_messages(), so it carries full
    conversation_history, unlike _gemini_generate()'s single system+user
    pair), used as generate_pitch()'s fallback when it needs the complete
    text at once rather than deltas."""
    system_instruction, contents = _messages_to_gemini(messages)
    config_kwargs: dict = {"thinking_config": _THINKING_MEDIUM}
    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction
    if max_output_tokens is not None:
        config_kwargs["max_output_tokens"] = max_output_tokens
    if temperature is not None:
        config_kwargs["temperature"] = temperature

    response = await _client.aio.models.generate_content(
        model=_MODEL_GEMINI, contents=contents, config=types.GenerateContentConfig(**config_kwargs)
    )
    _record_gemini_usage(response, call_name)
    return _TextResponse(response.text or "")


async def _gemini_generate_stream(
    *,
    call_name: str,
    messages: list[dict],
    max_output_tokens: int | None = None,
    temperature: float | None = None,
) -> AsyncIterator[str]:
    """Gemini equivalent of _openai_generate_stream(), used as the fallback
    when the initial OpenAI streaming call fails with
    _is_openai_quota_exhausted(). Records usage from the last streamed
    chunk's usage_metadata (Gemini repeats/accumulates it on every chunk,
    so the final one carries the complete totals) once the stream ends."""
    system_instruction, contents = _messages_to_gemini(messages)
    config_kwargs: dict = {"thinking_config": _THINKING_MEDIUM}
    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction
    if max_output_tokens is not None:
        config_kwargs["max_output_tokens"] = max_output_tokens
    if temperature is not None:
        config_kwargs["temperature"] = temperature

    last_chunk = None
    async for chunk in await _client.aio.models.generate_content_stream(
        model=_MODEL_GEMINI, contents=contents, config=types.GenerateContentConfig(**config_kwargs)
    ):
        last_chunk = chunk
        if chunk.text:
            yield chunk.text
    if last_chunk is not None:
        _record_gemini_usage(last_chunk, call_name)


# ---------------------------------------------------------------------------
# Situation Enricher — call this before classification
# ---------------------------------------------------------------------------

async def enrich_situation(situation: str, product: str) -> str:
    """
    Rewrites the raw situation text into a structured, professional 2-3
    sentence sales situation description (sales stage, buyer persona, core
    problem, what help is needed). The raw situation is often too terse or
    informal to match knowledge-card titles well in embedding space --
    this enriched version is what feeds expand_queries() and the final
    semantic rerank in _retrieve_cards().
    """
    response = await _client.aio.models.generate_content(
        model=_MODEL_GEMINI,
        contents=f"Product: {product}\nSituation: {situation}",
        config=types.GenerateContentConfig(
            system_instruction=SITUATION_ENRICHMENT_PROMPT,
            thinking_config=_THINKING_MINIMAL,
        ),
    )
    _record_gemini_usage(response, "enrich_situation")
    return response.text.strip()


async def extract_product_from_text(text: str) -> str:
    """Pulls a product/service description out of free-form text (the
    user's raw chat message) when it wasn't supplied as a separate
    structured field -- the chat composer only ever captures product via
    its own manual field, never from the typed message itself, so a
    product mentioned inline in prose would otherwise be silently lost.
    Returns "" if no product is clearly stated -- never guesses from
    industry/company context, matching FACTUAL ACCURACY elsewhere in this
    app."""
    response = await _client.aio.models.generate_content(
        model=_MODEL_GEMINI,
        contents=text,
        config=types.GenerateContentConfig(
            system_instruction=PRODUCT_EXTRACTION_PROMPT,
            thinking_config=_THINKING_MINIMAL,
        ),
    )
    _record_gemini_usage(response, "extract_product_from_text")
    # response.text is None (not raised) when Gemini returns no text part
    # at all -- e.g. a safety block or an empty candidate -- rather than
    # normal output; treat that the same as "nothing extracted" instead of
    # crashing on .strip().
    return (response.text or "").strip()


async def extract_website_url_from_text(text: str) -> str:
    """Pulls the prospect's website URL/domain out of free-form text when
    it wasn't supplied as a separate structured field -- mirrors
    extract_product_from_text(), with the same never-invent discipline:
    only surfaces a URL literally present in the text, never one
    constructed from a bare company name (the frontend's own regex-based
    extraction requires an http(s):// or www. prefix and only ever runs
    client-side, so this is the fallback for a bare domain or a caller
    that bypasses the frontend entirely)."""
    response = await _client.aio.models.generate_content(
        model=_MODEL_GEMINI,
        contents=text,
        config=types.GenerateContentConfig(
            system_instruction=WEBSITE_URL_EXTRACTION_PROMPT,
            thinking_config=_THINKING_MINIMAL,
        ),
    )
    _record_gemini_usage(response, "extract_website_url_from_text")
    # See extract_product_from_text() -- response.text can be None (not
    # raised) rather than an empty string on a blocked/empty response.
    return (response.text or "").strip()


# ---------------------------------------------------------------------------
# Methodology Detector — call this before query expansion
# ---------------------------------------------------------------------------

async def detect_methodology(situation: str) -> dict:
    """
    Identifies which single sales methodology/book best matches the
    situation and returns methodology-specific vocabulary. Feeds
    expand_queries() so query 4 (the book-concept angle) uses accurate,
    methodology-specific terms instead of generic ones.
    """
    prompt = METHODOLOGY_DETECTION_PROMPT.replace("{situation}", situation)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        max_output_tokens=200,
        thinking_config=_THINKING_MINIMAL,
    )
    try:
        try:
            response = await _client.aio.models.generate_content(
                model=_MODEL_GEMINI, contents=prompt, config=config
            )
        except errors.APIError:
            # Occasional malformed/rejected generation -- one retry clears
            # it almost always, same as generate_strategy()'s retry below.
            response = await _client.aio.models.generate_content(
                model=_MODEL_GEMINI, contents=prompt, config=config
            )
        _record_gemini_usage(response, "detect_methodology")
        return json.loads(response.text)
    except Exception:
        return {"methodology": "General", "reason": "", "key_terms": []}


# ---------------------------------------------------------------------------
# Output Format Detector — call this before deciding whether to route to
# the pitch track (generate_pitch) or the strategy track, and to narrow
# generate_pitch()'s output to only the format(s) actually requested.
# ---------------------------------------------------------------------------

# Keys mirror detect_output_format()'s possible return values (minus
# "strategy_only", which never reaches generate_pitch() -- see
# app/routers/chat.py's routing). Maps each format to the exact section
# template(s) to splice into PITCH_GENERATION_PROMPT's
# {sections_to_generate} -- the other section templates are never included
# in the prompt at all for a single-format request, so there's no
# "ignore the rest of this prompt" instruction for the model to override.
#
# "sales_pitch_full" (a generic "give me a pitch" ask, per the W2R RAG
# Addendum), "call_script_only" (explicit "cold call"/"call script"), and
# "sales_pitch_cold_call" (explicit "5R" ask) all route to the same
# SALES_PITCH_MERGED_PROMPT, which always defaults to the 5R cold-call
# structure internally -- cold call and generic "give me a pitch" are no
# longer two separate prompts. This is intentionally separate from
# "all_formats" (an explicit multi-channel ask like "email and WhatsApp"),
# which still uses the individual channel templates. The other
# "sales_pitch_<name>" keys let a Sales Engineer ask for a single named
# section of the 9-part sales pitch document (e.g. "give me the MAIN SALES
# PITCH") and get only that section back -- each maps directly to one
# SALES_PITCH_SUBSECTIONS entry plus the shared golden-rule footer (see
# detect_output_format()'s "sales_pitch_*" values, which mirror these dict
# keys one for one).
_PITCH_SECTION_TEMPLATES: dict[str, str] = {
    "email_only": EMAIL_SECTION_TEMPLATE,
    "whatsapp_only": WHATSAPP_SECTION_TEMPLATE,
    "meeting_script_only": SALES_PITCH_MEETING_SCRIPT_PROMPT + _SALES_PITCH_GOLDEN_RULE_FOOTER,
    "reengagement_only": SALES_PITCH_REENGAGEMENT_PROMPT + _SALES_PITCH_GOLDEN_RULE_FOOTER,
    "call_script_only": SALES_PITCH_MERGED_PROMPT,  # was COLD_CALL_SECTION_TEMPLATE
    "sales_pitch_full": SALES_PITCH_MERGED_PROMPT,  # was SALES_PITCH_PROSE_TEMPLATE
    "sales_pitch_core_value": SALES_PITCH_SUBSECTIONS["core_value"] + _SALES_PITCH_GOLDEN_RULE_FOOTER,
    "sales_pitch_elevator": SALES_PITCH_SUBSECTIONS["elevator"] + _SALES_PITCH_GOLDEN_RULE_FOOTER,
    "sales_pitch_main": SALES_PITCH_SUBSECTIONS["main"] + _SALES_PITCH_GOLDEN_RULE_FOOTER,
    "sales_pitch_cold_call": SALES_PITCH_MERGED_PROMPT,  # was SALES_PITCH_SUBSECTIONS["cold_call"] + footer
    "sales_pitch_persona": SALES_PITCH_SUBSECTIONS["persona"] + _SALES_PITCH_GOLDEN_RULE_FOOTER,
    "sales_pitch_discovery": SALES_PITCH_SUBSECTIONS["discovery"] + _SALES_PITCH_GOLDEN_RULE_FOOTER,
    "sales_pitch_followup": SALES_PITCH_SUBSECTIONS["followup"] + _SALES_PITCH_GOLDEN_RULE_FOOTER,
    "sales_pitch_objection": SALES_PITCH_SUBSECTIONS["objection"] + _SALES_PITCH_GOLDEN_RULE_FOOTER,
    "sales_pitch_next_step": SALES_PITCH_SUBSECTIONS["next_step"] + _SALES_PITCH_GOLDEN_RULE_FOOTER,
    "all_formats": "\n\n---\n\n".join(
        [WHATSAPP_SECTION_TEMPLATE, COLD_CALL_SECTION_TEMPLATE, EMAIL_SECTION_TEMPLATE]
    ),
}

# output_format values that resolve to SALES_PITCH_MERGED_PROMPT -- used by
# _build_regeneration_directive()'s call site in chat.py to decide when its
# code-driven "more detail"/"try another approach" directive applies. That
# directive's language (OPPORTUNITY POSITIONING's fixed type list, the
# 3-paragraph structure) is specific to SALES_PITCH_MERGED_PROMPT, so it
# should fire for every format that resolves to it, not just one of them.
_MERGED_PITCH_FORMATS = ("sales_pitch_full", "call_script_only", "sales_pitch_cold_call")

# Single-section "sales_pitch_*" requests (see above), listed once so both
# the token-budget map and the keyword fallback below can iterate them
# without repeating each key.
_SALES_PITCH_SECTION_FORMATS = tuple(
    f"sales_pitch_{name}" for name in SALES_PITCH_SUBSECTIONS
)

# generate_pitch()'s max_output_tokens, keyed by
# output_format. "sales_pitch_full" is a 9-part document (vs. the 1-3
# short sections every other format produces) and needs materially more
# budget to avoid truncating mid-document -- confirmed via _MODEL's docs
# this model family supports output well beyond 6000 tokens, so this is
# sized for the content, not clamped to a smaller-model limit. Each single
# "sales_pitch_*" section is comparable in size to one WHATSAPP/COLD_CALL
# section, so it reuses _DEFAULT_PITCH_MAX_OUTPUT_TOKENS via .get() at the
# call site rather than needing its own entry here.
_DEFAULT_PITCH_MAX_OUTPUT_TOKENS = 2500
_PITCH_MAX_OUTPUT_TOKENS: dict[str, int] = {
    "sales_pitch_full": 6000,
}

# generate_pitch() previously left temperature
# unset (provider default), which combined with near-identical context on
# consecutive follow-up turns produced near-duplicate wording turn after
# turn. A moderate temperature keeps output professional/on-template while
# giving legitimate re-generations room to actually vary.
_PITCH_TEMPERATURE = 0.6


_OUTPUT_FORMATS = (
    "email_only",
    "whatsapp_only",
    "call_script_only",
    "meeting_script_only",
    "reengagement_only",
    "sales_pitch_full",
    "all_formats",
    "strategy_only",
) + _SALES_PITCH_SECTION_FORMATS

# Keyword -> "sales_pitch_*" fallback map, used only when the classifier
# LLM call fails (see the try/except below). Order matters: checked in
# this order so a message naming a specific named section (e.g. "the main
# sales pitch") resolves to that section rather than falling through to
# the generic "pitch" catch-all further down.

_SALES_PITCH_SECTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sales_pitch_core_value", ("core value", "value proposition")),
    ("sales_pitch_elevator", ("elevator pitch", "elevator", "elevated pitch")),
    ("sales_pitch_main", ("main sales pitch", "main pitch", "main pitch script")),
    ("sales_pitch_cold_call", ("5r", "5-r")),
    ("sales_pitch_persona", ("persona",)),
    ("sales_pitch_discovery", ("discovery question", "discovery")),
    ("sales_pitch_followup", ("follow-up", "follow up", "followup")),
    ("sales_pitch_objection", ("objection",)),
    ("sales_pitch_next_step", ("next step", "next-step", "cta")),
)

async def detect_output_format(user_message: str) -> str:
    """
    Classifies what output format the user's latest message is asking for:
    "email_only", "whatsapp_only", "call_script_only", "sales_pitch_full",
    one of the "sales_pitch_*" single-section values (see
    _SALES_PITCH_SECTION_FORMATS), "all_formats", or "strategy_only".
    Drives both the pitch-vs-strategy routing decision and which
    section(s) generate_pitch() actually writes -- see
    _PITCH_SECTION_TEMPLATES above.

    Fails safe on any error using simple keyword matching: a message
    naming one of the sales pitch document's 9 named sections falls back
    to that section's "sales_pitch_*" value; a generic "pitch" ask (no
    section or channel named) falls back to "sales_pitch_full"; two or
    more channel keywords named together fall back to "all_formats"; one
    channel keyword alone falls back to its own *_only value; otherwise
    "strategy_only" -- matching the app's original pre-classifier behavior
    but now distinguishing "pitch" from an explicit multi-channel ask and
    from a single named section of the pitch document.
    """
    prompt = OUTPUT_FORMAT_DETECTION_PROMPT.replace("{message}", user_message)
    try:
        response = await _client.aio.models.generate_content(
            model=_MODEL_GEMINI,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                max_output_tokens=150,
                thinking_config=_THINKING_MINIMAL,
            ),
        )
        _record_gemini_usage(response, "detect_output_format")
        parsed = _extract_json(response.text)
        detected = parsed.get("format")
        if detected in _OUTPUT_FORMATS:
            return detected
    except Exception as exc:
        _logger.warning("detect_output_format failed, falling back: %s", exc)

    lowered = user_message.lower()
    channel_hits = {
    "email_only": "email" in lowered,
    "whatsapp_only": "whatsapp" in lowered,
    "call_script_only": any(k in lowered for k in ("cold call", "call script")),
    "meeting_script_only": any(k in lowered for k in ("meeting script", "script for my meeting", "script for the meeting")),
    "reengagement_only": any(k in lowered for k in ("re-engagement", "reengagement", "revival message", "revive this")),
}
    named_channels = [fmt for fmt, hit in channel_hits.items() if hit]
    if len(named_channels) >= 2:
        return "all_formats"

    for section_format, keywords in _SALES_PITCH_SECTION_KEYWORDS:
        if any(k in lowered for k in keywords):
            return section_format

    if len(named_channels) == 1:
        return named_channels[0]
    generic_pitch_keywords = ("pitch", "message", "draft", "script")
    return "sales_pitch_full" if any(k in lowered for k in generic_pitch_keywords) else "strategy_only"


# ---------------------------------------------------------------------------
# Message Intent Gate — call this before the full RAG/strategy pipeline on
# any non-follow-up turn, so a bare greeting or a genuinely off-topic
# message gets an instant, cheap, canned reply instead of being treated as
# a real (if minimal) sales situation -- see resolve_output_format() above
# for the analogous per-turn classification pattern this mirrors.
# ---------------------------------------------------------------------------

_MESSAGE_INTENTS = ("greeting", "off_topic", "sales_related")


async def classify_message_intent(message: str) -> str:
    """Returns "greeting", "off_topic", or "sales_related". Fails safe to
    "sales_related" on any classifier error or unrecognized value -- a
    gatekeeper hiccup must never block a legitimate sales request, only
    ever skip the (harmless) short-circuit."""
    prompt = MESSAGE_INTENT_PROMPT.replace("{message}", message)
    try:
        response = await _client.aio.models.generate_content(
            model=_MODEL_GEMINI,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                max_output_tokens=50,
                thinking_config=_THINKING_MINIMAL,
            ),
        )
        _record_gemini_usage(response, "classify_message_intent")
        parsed = _extract_json(response.text)
        intent = parsed.get("intent")
        if intent in _MESSAGE_INTENTS:
            return intent
    except Exception as exc:
        _logger.warning("classify_message_intent failed, defaulting to sales_related: %s", exc)
    return "sales_related"


# ---------------------------------------------------------------------------
# Pitch Follow-Up Classifiers — call these before generate_pitch() on a
# follow-up turn, to detect "more detail"/"try another approach" requests
# in code rather than relying on the model to notice them itself deep
# inside SALES_PITCH_MERGED_PROMPT. Verified against real follow-ups that
# a prompt-only rule (however specific, however many worked examples) gets
# lost among ~900 lines of other instructions -- the model kept reusing
# the same opportunity type/paragraph length regardless. Making the
# detection a guaranteed, separate step and handing the result to the
# model as a short directive (see chat.py's regeneration_directive
# building) is what actually changes behavior.
# ---------------------------------------------------------------------------

_PITCH_FEEDBACK_TYPES = ("more_detail", "regenerate_different_angle")

# Keys mirror OPPORTUNITY_TYPE_CLASSIFICATION_PROMPT's possible return
# values. Human-readable labels used when building the regeneration
# directive in chat.py -- kept here, next to the classifier that produces
# the keys, as the single source of truth both sides read from.
OPPORTUNITY_TYPE_LABELS: dict[str, str] = {
    "additional_source": "additional/alternate/backup manufacturing source",
    "capacity_support": "general production capacity support",
    "difficult_components": "specific hard-to-source or tight-tolerance components",
    "cost_review": "cost-review / re-quoting benchmark",
    "repeat_production": "source for ongoing repeat production",
}


async def classify_pitch_feedback(latest_request: str) -> str:
    """Returns "more_detail", "regenerate_different_angle", or "none".
    Fails safe to "none" on any classifier error -- a hiccup here should
    only skip the (helpful) regeneration directive, never block ordinary
    pitch generation."""
    prompt = PITCH_FEEDBACK_CLASSIFICATION_PROMPT.replace("{message}", latest_request)
    try:
        response = await _client.aio.models.generate_content(
            model=_MODEL_GEMINI,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                max_output_tokens=50,
                thinking_config=_THINKING_MINIMAL,
            ),
        )
        _record_gemini_usage(response, "classify_pitch_feedback")
        parsed = _extract_json(response.text)
        feedback_type = parsed.get("feedback_type")
        if feedback_type in _PITCH_FEEDBACK_TYPES:
            return feedback_type
    except Exception as exc:
        _logger.warning("classify_pitch_feedback failed, defaulting to none: %s", exc)
    return "none"


async def classify_pitch_opportunity_type(pitch_text: str) -> str:
    """Classifies which opportunity-type group a previously generated
    pitch used (see OPPORTUNITY_TYPE_LABELS above), so a regeneration can
    be told explicitly which group to avoid repeating. Fails safe to
    "unclear" -- the caller treats that as "can't compute a specific
    exclusion, fall back to a generic 'use a different angle' directive"
    rather than blocking regeneration."""
    prompt = OPPORTUNITY_TYPE_CLASSIFICATION_PROMPT.replace("{pitch_text}", pitch_text)
    try:
        response = await _client.aio.models.generate_content(
            model=_MODEL_GEMINI,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                max_output_tokens=50,
                thinking_config=_THINKING_MINIMAL,
            ),
        )
        _record_gemini_usage(response, "classify_pitch_opportunity_type")
        parsed = _extract_json(response.text)
        opportunity_type = parsed.get("opportunity_type")
        if opportunity_type in OPPORTUNITY_TYPE_LABELS:
            return opportunity_type
    except Exception as exc:
        _logger.warning("classify_pitch_opportunity_type failed, defaulting to unclear: %s", exc)
    return "unclear"


# ---------------------------------------------------------------------------
# Query Expander — call this before RAG search
# ---------------------------------------------------------------------------

async def expand_queries(
    situation: str,
    product: str,
    classification: dict,
    methodology_hint: dict | None = None,
) -> list[str]:
    """
    Converts the (enriched) situation into 6 specific search queries, one
    per angle: problem type, sales stage, persona, methodology, objection
    type, strategic objective. Use ALL of these to search your RAG
    embeddings separately. Combine results, deduplicate, take top N.

    This is why your relevance scores are 0.43 right now.
    With expanded queries they will reach 0.70+.
    """
    hint_text = ""
    if methodology_hint and methodology_hint.get("methodology") != "General":
        key_terms = ", ".join(methodology_hint.get("key_terms", []))
        methodology_name = methodology_hint.get("methodology", "")
        hint_text = f"""

CRITICAL INSTRUCTION FOR QUERY 4:
This situation has been identified as a {methodology_name} scenario.
Query 4 MUST use these exact methodology-specific terms: {key_terms}
Do not use generic terms for query 4. Use the specific
vocabulary listed above.
"""

    response = await _client.aio.models.generate_content(
        model=_MODEL_GEMINI,
        contents=(
            f"Product: {product}\n"
            f"Situation: {situation}\n"
            f"Sales Stage: {classification.get('sales_stage', 'unknown')}\n"
            f"Problem Type: {classification.get('problem_type', 'unknown')}\n"
            f"Buyer Persona: {classification.get('buyer_persona', 'unknown')}"
            f"{hint_text}"
        ),
        config=types.GenerateContentConfig(
            system_instruction=QUERY_EXPANSION_PROMPT,
            response_mime_type="application/json",
            thinking_config=_THINKING_MINIMAL,
        ),
    )
    _record_gemini_usage(response, "expand_queries")

    try:
        raw = response.text
        parsed = _extract_json(raw)
        # model may return {"queries": [...]} or just [...]
        if isinstance(parsed, list):
            return parsed
        for key in parsed:
            if isinstance(parsed[key], list):
                return parsed[key]
        return [situation]
    except Exception:
        return [situation]


# ---------------------------------------------------------------------------
# Company Summarizer
# ---------------------------------------------------------------------------

async def summarize_company(pages: list[tuple[str, str]], product: str) -> str:
    if not pages:
        return json.dumps({
            "company_name": "Unknown",
            "what_they_do": "No information could be retrieved from the website.",
            "products_or_services": [],
            "industries_they_serve": ["not mentioned"],
            "manufacturing_capabilities": ["not mentioned"],
            "certifications": "not mentioned",
            "company_size_signals": "not mentioned",
            "probable_buyer_personas": [],
            "facts_from_website": [],
            "sales_hypotheses": [],
            "red_flags": ["Website could not be scraped — strategy will be based on situation only."]
        })

    scraped_text = "\n\n".join(f"[{label}]\n{text}" for label, text in pages)

    system_prompt = f"""
You are a B2B industrial sales analyst helping a Sales Engineer
understand a prospect company before making their pitch.

A Sales Engineer is selling '{product}' and wants to understand
the company whose website has been scraped below.

YOUR JOB:
Read the scraped website text carefully and extract a structured
company profile. This profile will be used to generate a specific
sales strategy — so accuracy matters more than completeness.

STRICT RULES:
- Only use facts that are clearly present in the scraped text
- If something is not mentioned on the website, write "not mentioned"
- Never invent numbers, certifications, clients, or capabilities
- Clearly separate facts from hypotheses
- Keep everything concise — no long paragraphs

Return ONLY a valid JSON object. No explanation before or after.

{{
  "company_name": "",
  "what_they_do": "",
  "products_or_services": [],
  "industries_they_serve": [],
  "manufacturing_capabilities": [],
  "certifications": "",
  "company_size_signals": "",
  "probable_buyer_personas": [],
  "facts_from_website": [],
  "sales_hypotheses": [],
  "red_flags": []
}}

FIELD INSTRUCTIONS:

company_name
  The name of the company.

what_they_do
  One sentence. What is their core business.

products_or_services
  List of products or services mentioned on their website.

industries_they_serve
  Which industries are their customers in.
  If not mentioned write ["not mentioned"].

manufacturing_capabilities
  Technical capabilities like CNC machining, 5-axis,
  pressure testing, assembly etc.
  If not mentioned write ["not mentioned"].

certifications
  ISO, IATF, AS9100 etc.
  If none found write "not mentioned".

company_size_signals
  Any hint about size: employee count, number of plants,
  turnover, years in business, number of clients.
  If nothing found write "not mentioned".

probable_buyer_personas
  Based on what this company does and what '{product}' is —
  who inside their organization would most likely evaluate
  or approve this purchase?
  Examples: Purchase Manager, Design Head, Plant Head, MD,
  Vendor Development, Engineering Head.
  Think carefully. List 2-4 most likely personas.

facts_from_website
  List 3 to 5 specific facts you found directly on the website.
  These must be verifiable from the scraped text.
  Start each with "Website states:"

sales_hypotheses
  List 2 to 3 things you are inferring that are NOT directly
  stated on the website but seem likely based on what you read.
  Start each one with "Likely:" or "Probably:"
  These will be shown to the Sales Engineer as hypotheses
  to validate — not as facts.

red_flags
  Anything on the website that might make selling '{product}'
  to this company harder.
  Examples: they already manufacture this in-house,
  they mention a competitor as a partner,
  they seem too small or too large for this product.
  If nothing concerning write [].
"""
    try:
        response = await _openai_generate(
            call_name="summarize_company",
            system_instruction=system_prompt, user_content=scraped_text, json_mode=True
        )
    except OpenAIAPIError as exc:
        if not _is_openai_quota_exhausted(exc):
            raise
        _logger.warning("OpenAI quota exhausted -- falling back to Gemini for summarize_company")
        response = await _gemini_generate(
            call_name="summarize_company",
            system_instruction=system_prompt, user_content=scraped_text, json_mode=True
        )
    return response.text


# ---------------------------------------------------------------------------
# Situation Classifier
# ---------------------------------------------------------------------------

async def classify_situation(
    situation: str,
    product: str,
    candidate_types: list[str],
) -> dict:
    """
    Returns a full classification dict, not just a single category string.

    Dict shape:
    {
        "sales_stage": str,
        "problem_type": str,       <- one of candidate_types
        "buyer_persona": str,
        "objective": str,
        "missing_information": []
    }

    The caller can still use classification["problem_type"] wherever
    the old single-string return value was used.
    """
    system_prompt = f"""
You are a B2B industrial sales situation analyst.

A Sales Engineer has described their sales situation. Read it carefully
and extract structured information from it.

ALLOWED VALUES:

sales_stage — pick ONE:
prospecting, initial_contact, first_meeting, discovery,
qualification, proposal, negotiation, follow_up, stuck, revival

problem_type — pick ONE from this list:
{", ".join(candidate_types)}

buyer_persona — extract from the situation text, pick ONE:
purchase_manager, MD, CEO, engineer, design_head,
vendor_development, plant_head, sales_head, gatekeeper, unknown

objective — ONE short sentence: what does the salesperson
need to achieve right now?

missing_information — list 2 to 3 important things the salesperson
did NOT mention that would change the strategy.
Examples: "whether engineering team has been contacted",
          "what was shown in the previous meeting",
          "whether a trial or sample was offered".
If nothing important is missing, return [].

Return ONLY a valid JSON object. No explanation before or after.

{{
  "sales_stage": "",
  "problem_type": "",
  "buyer_persona": "",
  "objective": "",
  "missing_information": []
}}
"""
    response = await _client.aio.models.generate_content(
        model=_MODEL_GEMINI,
        contents=f"Product: {product}\nSituation: {situation}",
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            thinking_config=_THINKING_MINIMAL,
        ),
    )
    _record_gemini_usage(response, "classify_situation")

    parsed = _extract_json(response.text)

    # make sure problem_type is a known value — fallback gracefully
    if parsed.get("problem_type") not in candidate_types:
        parsed["problem_type"] = parsed.get("problem_type", "unknown")

    return parsed


# ---------------------------------------------------------------------------
# Strategy Generator
# ---------------------------------------------------------------------------

_STRATEGY_JSON_SCHEMA = """{
  "situation_summary": "2-3 sentences on what is actually happening",
  "whats_probably_going_on": "the real reason behind the buyer behaviour",
  "objective": "one sentence — the single thing to achieve next",
  "recommended_strategy": [
    "step 1 — specific action",
    "step 2 — specific action",
    "step 3 — specific action"
  ],
  "who_to_approach": "which person or role, and exactly why",
  "questions_to_ask": [
    "specific question 1",
    "specific question 2",
    "specific question 3",
    "specific question 4"
  ],
  "what_to_say": "how to position the product in this specific situation",
  "email_or_whatsapp_draft": "a short ready-to-send message",
  "what_not_to_do": [
    "specific mistake 1",
    "specific mistake 2",
    "specific mistake 3"
  ],
  "authority_note": "Only include this field when the situation involves pricing, discounts, or commercial decisions. Two short bullets: first — what the SE can decide right now without asking anyone. Second — what needs manager approval before acting. If the situation states a discount is already authorized, say when and how to use it strategically — do NOT say check with your manager for something already approved. Leave this field empty string for simple prospecting or follow-up situations with no commercial decisions.",
  "next_action": "one specific concrete action — never empty, never vague"
}"""

async def generate_strategy(
    company_snapshot: str,
    situation_classification: dict,
    product: str,
    situation: str,
    context_entries: list[KnowledgeEntry],
    conversation_memory: str = "",
    feedback_context: str = "",
) -> dict:
    """
    NOTE: situation_classification is now a DICT (not a string).
    It comes from classify_situation() which now returns full classification.

    conversation_memory/feedback_context are optional background-context
    blocks (see _format_memory_and_feedback_blocks) -- never directives.
    """
    context = _format_context(context_entries)
    company_context = _format_company_snapshot(company_snapshot)
    memory_block, feedback_block = _format_memory_and_feedback_blocks(
        conversation_memory, feedback_context
    )

    # pull useful fields from classification for the prompt
    sales_stage = situation_classification.get("sales_stage", "unknown")
    problem_type = situation_classification.get("problem_type", "unknown")
    buyer_persona = situation_classification.get("buyer_persona", "unknown")
    objective = situation_classification.get("objective", "unknown")
    missing_info = situation_classification.get("missing_information", [])

    missing_info_text = (
        "\n".join(f"  - {m}" for m in missing_info)
        if missing_info
        else "  None identified"
    )

    system_prompt = f"""
You are a Senior B2B Industrial Sales Director with 20 years of experience
in industrial and manufacturing sales in India.

A Sales Engineer has come to you with a specific sales challenge. You have
been given a company profile researched from their website, a classification
of the situation, relevant knowledge cards from proven sales books, and the
original situation described by the Sales Engineer.

YOUR JOB:
Give specific, practical advice the Sales Engineer can act on immediately.
You are not a chatbot. You are a senior sales leader giving real guidance
to a junior salesperson in the field.

STRICT RULES:
1. Never give generic advice like "highlight your quality" or
   "be persistent" — these are useless.
2. Every recommendation must relate directly to THIS company,
   THIS product, THIS situation.
3. Use the retrieved knowledge cards to back your strategy —
   cite them as [1], [2] etc. inside the relevant text fields.
4. Never invent facts about the company beyond the company profile.
5. next_action must be ONE specific action — never empty, never vague.
6. what_not_to_do must contain ONLY mistake descriptions —
   maximum 3 items — nothing else in that array.
7. If missing_information is listed below, acknowledge it briefly
   in situation_summary and factor it into your strategy.
8. Never suggest specific commercial mechanisms unless the situation
   explicitly authorizes them. Do not introduce:
   - Pilot orders, trial batches, or test batches
   - Free samples or no-cost service offers
   - Volume commitments, annual contracts, or long-term agreements
   - Payment term changes
   - Consignment stock
   - Delivery guarantees
   - Exclusivity arrangements
   - Any commercial concession not mentioned by the Sales Engineer

   This rule overrides knowledge cards. A card recommending
   "non-monetary terms" or "volume pricing" does not authorize
   you to invent specific commercial offers.

   Also wrong: "Pair the discount with a request for faster payment
   cycles or guaranteed volumes" — unauthorized even when framed
   as something to ask for rather than offer.
   Also wrong: "Explore process efficiencies like shorter lead times
   or improved batch tracking" — unauthorized unless mentioned.

   Correct: "If price remains a hard blocker, there may be non-price
   levers worth exploring — check with your manager on what options
   are available before your next meeting."
   Wrong: "Offer 60-day payment terms or a 12-month contract."

9. Be clear about what the SE can decide versus what needs approval.

   The SE can decide RIGHT NOW without asking anyone:
   - Which person to contact
   - What questions to ask
   - How to frame the conversation
   - What meeting to request
   - Sending an email or WhatsApp message
   - Any action that involves no commercial commitment

   The SE needs manager approval BEFORE acting:
   - Offering any price reduction beyond what is already authorized
   - Changing payment terms
   - Making delivery commitments
   - Offering any commercial concession not in the situation

   IMPORTANT: If the situation explicitly states a discount is already
   authorized — for example "our MD has authorized a maximum 10%
   reduction" — do NOT tell the SE to check with their manager before
   using it. It is already approved. Tell them when and how to use it
   strategically instead.

   Only write "check with your manager" when something genuinely has
   NOT been authorized. Never apply this warning to routine sales
   actions.

SITUATION CLASSIFICATION:
  Sales Stage: {sales_stage}
  Problem Type: {problem_type}
  Buyer Persona: {buyer_persona}
  Objective: {objective}

MISSING INFORMATION (flagged — factor this into your advice):
{missing_info_text}

COMPANY PROFILE:
{company_context}

KNOWLEDGE CARDS:
{context}
{memory_block}{feedback_block}
Respond with ONLY a valid JSON object matching exactly this shape.
No markdown. No text before or after the JSON.

{_STRATEGY_JSON_SCHEMA}
"""
    contents = f"Product: {product}\nSituation: {situation}"

    # Retries cover the API call AND the JSON parse: occasional malformed
    # JSON for this large a schema, and transient rate-limit/server errors
    # (surfaced as OpenAIAPIError) usually clear on one retry too.
    last_error: Exception | None = None
    parsed: dict | None = None
    for attempt in range(2):
        try:
            try:
                response = await _openai_generate(
                    call_name="generate_strategy",
                    system_instruction=system_prompt,
                    user_content=contents,
                    json_mode=True,
                    # This schema comfortably fits in far less -- capping it
                    # keeps response length (and cost/latency) bounded.
                    max_output_tokens=1500,
                )
            except OpenAIAPIError as exc:
                if not _is_openai_quota_exhausted(exc):
                    raise
                _logger.warning("OpenAI quota exhausted -- falling back to Gemini for generate_strategy")
                response = await _gemini_generate(
                    call_name="generate_strategy",
                    system_instruction=system_prompt,
                    user_content=contents,
                    json_mode=True,
                    max_output_tokens=1500,
                )
            parsed = _extract_json(response.text)
            break
        except OpenAIAPIError as exc:
            last_error = exc
            _logger.warning(
                "generate_strategy attempt %d/%d failed: %s", attempt + 1, 2, exc
            )
        except (json.JSONDecodeError, re.error) as exc:
            last_error = exc
            _logger.warning(
                "generate_strategy attempt %d/%d produced unparsable JSON: %s",
                attempt + 1,
                2,
                exc,
            )
    if parsed is None:
        assert last_error is not None
        raise last_error

    # safety — next_action must never be empty
    if not parsed.get("next_action", "").strip():
        parsed["next_action"] = (
            "Identify the right person to contact next and reach out "
            "with a specific question — not a generic follow-up."
        )

    # safety — what_not_to_do must be a clean list
    wntd = parsed.get("what_not_to_do", [])
    if not isinstance(wntd, list):
        parsed["what_not_to_do"] = [str(wntd)]

    return {
        "company_snapshot": company_snapshot,
        "situation_classification": situation_classification,
        "situation_summary": parsed.get("situation_summary", ""),
        "whats_probably_going_on": parsed.get("whats_probably_going_on", ""),
        "objective": parsed.get("objective", ""),
        "recommended_strategy": parsed.get("recommended_strategy", []),
        "who_to_approach": parsed.get("who_to_approach", ""),
        "questions_to_ask": parsed.get("questions_to_ask", []),
        "what_to_say": parsed.get("what_to_say", ""),
        "email_or_whatsapp_draft": parsed.get("email_or_whatsapp_draft", ""),
        "what_not_to_do": parsed.get("what_not_to_do", []),
        "next_action": parsed.get("next_action", ""),
    }


# ---------------------------------------------------------------------------
# Narrative Strategy Generator — streamed markdown version of
# generate_strategy(), used only by the /strategy/stream endpoint. The
# structured JSON call above stays the source of truth for StrategyResponse;
# this produces the same advice as flowing prose, token-by-token.
# ---------------------------------------------------------------------------


async def generate_narrative_strategy(
    company_snapshot: str,
    situation_classification: dict,
    product: str,
    situation: str,
    context_entries: list[KnowledgeEntry],
    conversation_memory: str = "",
    feedback_context: str = "",
    focused_followup: bool = False,
    enriched_situation: str = "",
    conversation_history: list[dict] | None = None,
) -> AsyncIterator[str]:
    """Async generator yielding narrative text deltas as they stream from
    Gemini. Mirrors generate_strategy()'s prompt inputs but produces
    markdown prose, not JSON -- never uses response_mime_type=json.

    conversation_history, if given, is a chronological list of
    {"role": "user"|"assistant", "content": str} turns (see
    app/routers/chat.py's conversation-history helper) inserted before the
    current turn's final message, so the model sees the actual prior
    exchanges rather than only the rolling memory summary already folded
    into conversation_memory/memory_block above.

    focused_followup=True switches to FOLLOWUP_RESPONSE_PROMPT -- a
    separate, much shorter prompt with no rigid section format for the
    model to override -- instead of trying to bolt a "skip the format"
    conditional onto STRATEGY_NARRATIVE_PROMPT (that pattern proved
    unreliable for the company-mismatch guard and the email-inclusion
    rule: instructions competing with "follow this structure exactly"
    routinely lost)."""
    company_context = _format_company_snapshot(company_snapshot)

    if focused_followup:
        system_prompt = FOLLOWUP_RESPONSE_PROMPT.format(
            enriched_situation=enriched_situation or "(no prior context)",
            company_context=company_context,
            memory_context=conversation_memory or "(none yet)",
            feedback_context=feedback_context or "(none yet)",
        )
        max_output_tokens = 600
    else:
        context = _format_context(context_entries)
        memory_block, feedback_block = _format_memory_and_feedback_blocks(
            conversation_memory, feedback_context
        )

        sales_stage = situation_classification.get("sales_stage", "unknown")
        problem_type = situation_classification.get("problem_type", "unknown")
        buyer_persona = situation_classification.get("buyer_persona", "unknown")
        objective = situation_classification.get("objective", "unknown")
        missing_info = situation_classification.get("missing_information", [])
        missing_info_text = (
            "\n".join(f"  - {m}" for m in missing_info) if missing_info else "  None identified"
        )

        system_prompt = STRATEGY_NARRATIVE_PROMPT.format(
            sales_stage=sales_stage,
            problem_type=problem_type,
            buyer_persona=buyer_persona,
            objective=objective,
            missing_info_text=missing_info_text,
            company_context=company_context,
            context=context,
            memory_block=memory_block,
            feedback_block=feedback_block,
        )
        max_output_tokens = 1800

    messages = _to_openai_messages(
        conversation_history,
        f"Product: {product}\nSituation: {situation}",
        system_instruction=system_prompt,
    )
    yielded_any = False
    try:
        async for delta in _openai_generate_stream(
            call_name="generate_narrative_strategy", messages=messages, max_output_tokens=max_output_tokens
        ):
            yielded_any = True
            yield delta
        return
    except OpenAIAPIError as exc:
        # Only safe to switch providers if nothing has reached the client
        # yet -- once tokens have started streaming there's no clean way to
        # retry mid-response.
        if yielded_any or not _is_openai_quota_exhausted(exc):
            raise
        _logger.warning(
            "OpenAI quota exhausted -- falling back to Gemini for generate_narrative_strategy"
        )
    async for delta in _gemini_generate_stream(
        call_name="generate_narrative_strategy", messages=messages, max_output_tokens=max_output_tokens
    ):
        yield delta


# ---------------------------------------------------------------------------
# BD Strategy Generator — structured-JSON counterpart of generate_strategy(),
# used by the non-streaming POST /bd-chat/.../strategy endpoint. MOTM's own
# BD team sells MOTM itself, so unlike generate_strategy() there is no
# "company profile" of the thing being sold to analyze -- company_snapshot
# here (when non-empty) is a snapshot of the PROSPECT (who MOTM is selling
# TO), built only if a prospect website was supplied; see
# app/routers/bd_chat.py's company_snapshot handling. Reuses
# StrategyResponse/generate_strategy()'s exact output shape (per the product
# brief -- BD's output shape is identical to SE's) but reframes the system
# prompt around "you ARE the seller" and adds Rule 3 (prefer MOTM's own
# retrieved knowledge over generic sales-methodology principles).
# ---------------------------------------------------------------------------


async def generate_bd_strategy(
    prospect_snapshot: str,
    situation_classification: dict,
    situation: str,
    context_entries: list[KnowledgeEntry],
    conversation_memory: str = "",
    feedback_context: str = "",
) -> dict:
    """BD counterpart of generate_strategy(). prospect_snapshot is "" (not
    "Unknown"-JSON like SE's company_snapshot always is) when no
    prospect_website was supplied for this turn -- callers must format it
    themselves before interpolating (see _format_company_snapshot's
    "not valid JSON -> return as-is" fallback, which already handles an
    empty string safely by producing an empty formatted block)."""
    context = _format_context(context_entries)
    prospect_context = (
        _format_company_snapshot(prospect_snapshot)
        if prospect_snapshot
        else "(no prospect website supplied -- work from the situation description only)"
    )
    memory_block, feedback_block = _format_memory_and_feedback_blocks(
        conversation_memory, feedback_context
    )

    sales_stage = situation_classification.get("sales_stage", "unknown")
    problem_type = situation_classification.get("problem_type", "unknown")
    buyer_persona = situation_classification.get("buyer_persona", "unknown")
    objective = situation_classification.get("objective", "unknown")
    missing_info = situation_classification.get("missing_information", [])
    missing_info_text = (
        "\n".join(f"  - {m}" for m in missing_info) if missing_info else "  None identified"
    )

    system_prompt = f"""
You are a Senior Business Development Director at MOTM with 20 years of
experience selling B2B industrial sourcing/manufacturing services in India.

CRITICAL FRAME: unlike a Sales Engineer pitching a customer's own product,
here YOU (through the BD rep you are advising) ARE THE SELLER and the
product being sold IS MOTM ITSELF. A "prospect snapshot" below, if present,
describes WHO MOTM is selling TO -- never MOTM's own capabilities.

A member of MOTM's Business Development team has come to you with a
specific opportunity. You have been given a prospect snapshot (if a website
was supplied), a classification of the situation, relevant MOTM knowledge
cards (positioning, pricing, ICP, objections, sales process, case studies),
and the original situation described by the BD rep.

YOUR JOB:
Give specific, practical advice the BD rep can act on immediately to move
this MOTM opportunity forward. You are not a chatbot. You are a senior BD
leader giving real guidance to a junior colleague in the field.

STRICT RULES:
1. Never give generic advice like "highlight your quality" or "be
   persistent" -- these are useless.
2. Every recommendation must relate directly to THIS prospect (if known)
   and THIS situation, not a generic MOTM pitch.
3. PREFER MOTM'S OWN KNOWLEDGE: when both an MOTM-specific card (about
   MOTM's own positioning, pricing, ICP, objection handling, or case
   studies) and a generic sales-methodology card are relevant, lead with
   and prioritize the MOTM-specific one -- it reflects what actually works
   selling MOTM. Use a generic methodology card only to add structure or
   fill a gap MOTM-specific cards don't cover.
4. Cite knowledge cards you use as [1], [2] etc. inside the relevant text
   fields.
5. Never invent facts about the prospect beyond the prospect snapshot, and
   never invent facts about MOTM beyond the retrieved knowledge cards.
6. next_action must be ONE specific action -- never empty, never vague.
7. what_not_to_do must contain ONLY mistake descriptions -- maximum 3
   items -- nothing else in that array.
8. If missing_information is listed below, acknowledge it briefly in
   situation_summary and factor it into your strategy.
9. Never suggest specific commercial mechanisms (discounts, pilot/trial
   engagements, extended payment terms, exclusivity) unless the situation
   or the retrieved MOTM pricing/positioning cards explicitly authorize
   them. This rule overrides generic-methodology cards.
10. Be clear about what the BD rep can decide right now (framing,
    outreach, meeting requests, questions to ask) versus what needs
    manager approval (any pricing or commercial concession beyond what is
    already authorized). If the situation states something is already
    authorized, do not say to check with a manager for it.

SITUATION CLASSIFICATION:
  Sales Stage: {sales_stage}
  Problem Type: {problem_type}
  Buyer Persona: {buyer_persona}
  Objective: {objective}

MISSING INFORMATION (flagged — factor this into your advice):
{missing_info_text}

PROSPECT SNAPSHOT (who MOTM is selling TO -- not MOTM's own profile):
{prospect_context}

MOTM KNOWLEDGE CARDS:
{context}
{memory_block}{feedback_block}
Respond with ONLY a valid JSON object matching exactly this shape.
No markdown. No text before or after the JSON.

{_STRATEGY_JSON_SCHEMA}
"""
    contents = f"Situation: {situation}"

    last_error: Exception | None = None
    parsed: dict | None = None
    for attempt in range(2):
        try:
            try:
                response = await _openai_generate(
                    call_name="generate_bd_strategy",
                    system_instruction=system_prompt,
                    user_content=contents,
                    json_mode=True,
                    max_output_tokens=1500,
                )
            except OpenAIAPIError as exc:
                if not _is_openai_quota_exhausted(exc):
                    raise
                _logger.warning("OpenAI quota exhausted -- falling back to Gemini for generate_bd_strategy")
                response = await _gemini_generate(
                    call_name="generate_bd_strategy",
                    system_instruction=system_prompt,
                    user_content=contents,
                    json_mode=True,
                    max_output_tokens=1500,
                )
            parsed = _extract_json(response.text)
            break
        except OpenAIAPIError as exc:
            last_error = exc
            _logger.warning(
                "generate_bd_strategy attempt %d/%d failed: %s", attempt + 1, 2, exc
            )
        except (json.JSONDecodeError, re.error) as exc:
            last_error = exc
            _logger.warning(
                "generate_bd_strategy attempt %d/%d produced unparsable JSON: %s",
                attempt + 1,
                2,
                exc,
            )
    if parsed is None:
        assert last_error is not None
        raise last_error

    if not parsed.get("next_action", "").strip():
        parsed["next_action"] = (
            "Identify the right person to contact next and reach out "
            "with a specific question — not a generic follow-up."
        )

    wntd = parsed.get("what_not_to_do", [])
    if not isinstance(wntd, list):
        parsed["what_not_to_do"] = [str(wntd)]

    return {
        "company_snapshot": prospect_snapshot,
        "situation_classification": situation_classification,
        "situation_summary": parsed.get("situation_summary", ""),
        "whats_probably_going_on": parsed.get("whats_probably_going_on", ""),
        "objective": parsed.get("objective", ""),
        "recommended_strategy": parsed.get("recommended_strategy", []),
        "who_to_approach": parsed.get("who_to_approach", ""),
        "questions_to_ask": parsed.get("questions_to_ask", []),
        "what_to_say": parsed.get("what_to_say", ""),
        "email_or_whatsapp_draft": parsed.get("email_or_whatsapp_draft", ""),
        "what_not_to_do": parsed.get("what_not_to_do", []),
        "next_action": parsed.get("next_action", ""),
    }


# ---------------------------------------------------------------------------
# BD Narrative Strategy Generator — streamed markdown version of
# generate_bd_strategy(), used only by POST /bd-chat/.../strategy/stream.
# Mirrors generate_narrative_strategy()'s structure/provider-fallback
# exactly, swapping in BD_STRATEGY_NARRATIVE_PROMPT.
# ---------------------------------------------------------------------------


async def generate_bd_narrative_strategy(
    prospect_snapshot: str,
    situation_classification: dict,
    situation: str,
    context_entries: list[KnowledgeEntry],
    conversation_memory: str = "",
    feedback_context: str = "",
    focused_followup: bool = False,
    enriched_situation: str = "",
    conversation_history: list[dict] | None = None,
) -> AsyncIterator[str]:
    """BD counterpart of generate_narrative_strategy() -- see that
    function's docstring for the streaming/provider-fallback contract,
    which this mirrors exactly. focused_followup still uses the shared,
    persona-agnostic FOLLOWUP_RESPONSE_PROMPT (a short "just answer the
    follow-up" prompt with no rigid section format) since that prompt
    doesn't reference "company being sold" language."""
    prospect_context = (
        _format_company_snapshot(prospect_snapshot)
        if prospect_snapshot
        else "(no prospect website supplied -- work from the situation description only)"
    )

    if focused_followup:
        system_prompt = FOLLOWUP_RESPONSE_PROMPT.format(
            enriched_situation=enriched_situation or "(no prior context)",
            company_context=prospect_context,
            memory_context=conversation_memory or "(none yet)",
            feedback_context=feedback_context or "(none yet)",
        )
        max_output_tokens = 600
    else:
        context = _format_context(context_entries)
        memory_block, feedback_block = _format_memory_and_feedback_blocks(
            conversation_memory, feedback_context
        )

        sales_stage = situation_classification.get("sales_stage", "unknown")
        problem_type = situation_classification.get("problem_type", "unknown")
        buyer_persona = situation_classification.get("buyer_persona", "unknown")
        objective = situation_classification.get("objective", "unknown")
        missing_info = situation_classification.get("missing_information", [])
        missing_info_text = (
            "\n".join(f"  - {m}" for m in missing_info) if missing_info else "  None identified"
        )

        system_prompt = BD_STRATEGY_NARRATIVE_PROMPT.format(
            sales_stage=sales_stage,
            problem_type=problem_type,
            buyer_persona=buyer_persona,
            objective=objective,
            missing_info_text=missing_info_text,
            company_context=prospect_context,
            context=context,
            memory_block=memory_block,
            feedback_block=feedback_block,
        )
        max_output_tokens = 1800

    messages = _to_openai_messages(
        conversation_history,
        f"Situation: {situation}",
        system_instruction=system_prompt,
    )
    yielded_any = False
    try:
        async for delta in _openai_generate_stream(
            call_name="generate_bd_narrative_strategy", messages=messages, max_output_tokens=max_output_tokens
        ):
            yielded_any = True
            yield delta
        return
    except OpenAIAPIError as exc:
        if yielded_any or not _is_openai_quota_exhausted(exc):
            raise
        _logger.warning(
            "OpenAI quota exhausted -- falling back to Gemini for generate_bd_narrative_strategy"
        )
    async for delta in _gemini_generate_stream(
        call_name="generate_bd_narrative_strategy", messages=messages, max_output_tokens=max_output_tokens
    ):
        yield delta


# ---------------------------------------------------------------------------
# Hiring-Signal Outreach Agent — two-stage pipeline for
# POST /bd-chat/.../hiring-signal-outreach (see app/routers/bd_chat.py).
#
# Stage 1 (generate_bd_hiring_signal_analysis): a plain-text call using
# BD_HIRING_SIGNAL_ANALYSIS_PROMPT, which analyzes the hiring signal into a
# structured markdown report (hiring role, commercial interpretation,
# expansion hypothesis, confidence, evidence classification) and
# deliberately does NOT write outreach copy or position MOTM -- see that
# prompt's own STRICT RULES section.
#
# Stage 2 (generate_bd_hiring_signal_outreach): a structured-JSON call
# using BD_HIRING_SIGNAL_OUTREACH_PROMPT, which takes stage 1's report as
# its source of truth for the hiring role/commercial interpretation (does
# not re-derive it) and writes the actual WhatsApp sequence + canned
# replies, grounded in the retrieved MOTM knowledge cards.
#
# Both are modeled on generate_bd_strategy()'s provider/fallback pattern
# (OpenAI primary, Gemini fallback on quota exhaustion, one retry on
# unparsable JSON for the structured stage).
# ---------------------------------------------------------------------------


async def generate_bd_hiring_signal_analysis(
    company_name: str,
    company_website: str,
    job_post_text: str,
    hiring_role: str,
    location: str,
    notes: str,
) -> str:
    """Stage 1: returns the raw markdown analysis report (not JSON —
    BD_HIRING_SIGNAL_ANALYSIS_PROMPT's OUTPUT FORMAT is a fixed set of
    markdown sections, not a JSON schema)."""
    system_prompt = BD_HIRING_SIGNAL_ANALYSIS_PROMPT.format(
        company_name=company_name or "(not supplied)",
        company_website=company_website or "(not supplied)",
        hiring_role=hiring_role or "(not supplied)",
        location=location or "(not supplied)",
        job_post_text=job_post_text or "(not supplied)",
        notes=notes or "(not supplied)",
    )
    contents = "Analyze the hiring signal described above."

    try:
        response = await _openai_generate(
            call_name="generate_bd_hiring_signal_analysis",
            system_instruction=system_prompt,
            user_content=contents,
            max_output_tokens=1200,
        )
    except OpenAIAPIError as exc:
        if not _is_openai_quota_exhausted(exc):
            raise
        _logger.warning(
            "OpenAI quota exhausted -- falling back to Gemini for generate_bd_hiring_signal_analysis"
        )
        response = await _gemini_generate(
            call_name="generate_bd_hiring_signal_analysis",
            system_instruction=system_prompt,
            user_content=contents,
            max_output_tokens=1200,
        )
    return response.text.strip()


_HIRING_SIGNAL_OUTREACH_JSON_SCHEMA = """{
  "company_understanding": {
    "products_services": "what this company makes/sells, from the supplied info",
    "industries_applications": "industries and applications they serve",
    "typical_buyers": "who typically buys from them",
    "business_type": "e.g. OEM manufacturer, job-shop/contract manufacturer, distributor, services company"
  },
  "commercial_interpretation": {
    "why_hiring": "one concise sentence — extracted from the signal analysis's commercial interpretation, not re-derived, on what commercial result this hire is probably meant to achieve",
    "business_objective": "one concise sentence — the signal analysis's primary business objective",
    "expansion_opportunity": "one concise sentence — the signal analysis's commercial expansion hypothesis, restated concisely"
  },
  "motm_fit": {
    "positioning": "exactly one of: Industrial BD Extension | Enable Technical Sales | Market Entry + Execution",
    "relevant_capabilities": ["2-4 MOTM capabilities, grounded only in the retrieved knowledge cards, most relevant to this company/role"],
    "key_differentiators": ["1-3 differentiators, grounded only in the retrieved knowledge cards"],
    "why_relevant": "1-2 sentences tying the capabilities/differentiators directly to this company's specific situation"
  },
  "whatsapp_messages": {
    "message_1": "80-110 words — introduction, hiring trigger, company understanding, expansion hypothesis, positioning, one differentiator, low-pressure CTA",
    "message_2": "55-85 words — credibility, why MOTM is different, complements their internal team",
    "message_3": "35-55 words — summarize relevance, low-pressure, easy to answer"
  },
  "response_handling": {
    "send_details": "a short, company-specific reply to 'send details'",
    "what_do_you_do": "a short, company-specific reply to 'what exactly do you do?'",
    "already_hiring": "a short, company-specific reply to 'we are already hiring someone'",
    "not_interested": "a short, company-specific reply to 'not interested'"
  }
}"""


async def generate_bd_hiring_signal_outreach(
    signal_analysis: str,
    company_name: str,
    company_website: str,
    job_post_text: str,
    hiring_role: str,
    location: str,
    contact_details: str,
    sender_name: str,
    notes: str,
    context_entries: list[KnowledgeEntry],
) -> dict:
    """Stage 2: returns a dict matching BDHiringSignalResponse's fields
    minus id/sources/signal_analysis (the router fills id/sources in from
    the persisted Message/MessageSource rows, and signal_analysis is
    stage 1's own return value, not part of this call's output)."""
    context = _format_context(context_entries)

    system_prompt = BD_HIRING_SIGNAL_OUTREACH_PROMPT.format(
        signal_analysis=signal_analysis,
        context=context,
        company_name=company_name or "(not supplied)",
        company_website=company_website or "(not supplied)",
        hiring_role=hiring_role or "(not supplied)",
        location=location or "(not supplied)",
        contact_details=contact_details or "(not supplied)",
        sender_name=sender_name or "(not supplied)",
        job_post_text=job_post_text or "(not supplied)",
        notes=notes or "(not supplied)",
        json_schema=_HIRING_SIGNAL_OUTREACH_JSON_SCHEMA,
    )
    contents = "Write the outreach package for the company described above."

    last_error: Exception | None = None
    parsed: dict | None = None
    for attempt in range(2):
        try:
            try:
                response = await _openai_generate(
                    call_name="generate_bd_hiring_signal_outreach",
                    system_instruction=system_prompt,
                    user_content=contents,
                    json_mode=True,
                    max_output_tokens=1500,
                )
            except OpenAIAPIError as exc:
                if not _is_openai_quota_exhausted(exc):
                    raise
                _logger.warning(
                    "OpenAI quota exhausted -- falling back to Gemini for generate_bd_hiring_signal_outreach"
                )
                response = await _gemini_generate(
                    call_name="generate_bd_hiring_signal_outreach",
                    system_instruction=system_prompt,
                    user_content=contents,
                    json_mode=True,
                    max_output_tokens=1500,
                )
            parsed = _extract_json(response.text)
            break
        except OpenAIAPIError as exc:
            last_error = exc
            _logger.warning(
                "generate_bd_hiring_signal_outreach attempt %d/%d failed: %s", attempt + 1, 2, exc
            )
        except (json.JSONDecodeError, re.error) as exc:
            last_error = exc
            _logger.warning(
                "generate_bd_hiring_signal_outreach attempt %d/%d produced unparsable JSON: %s",
                attempt + 1,
                2,
                exc,
            )
    if parsed is None:
        assert last_error is not None
        raise last_error

    return {
        "company_understanding": parsed.get("company_understanding", {}),
        "commercial_interpretation": parsed.get("commercial_interpretation", {}),
        "motm_fit": parsed.get("motm_fit", {}),
        "whatsapp_messages": parsed.get("whatsapp_messages", {}),
        "response_handling": parsed.get("response_handling", {}),
    }


# ---------------------------------------------------------------------------
# Company/Situation Match Check — run as a discrete step in chat.py BEFORE
# generate_narrative_strategy() is called, not embedded inside its prompt.
# The mismatch check used to live inside STRATEGY_NARRATIVE_PROMPT, but
# sharing a context window with the much larger strategy-writing
# instructions meant the model routinely overrode the refusal condition
# (e.g. seeing "hydraulic" anywhere in a company profile and concluding a
# match, even for a services company that merely has a hydraulics
# alliance partner). A short, single-purpose prompt with nothing else
# competing for the model's attention is far more reliable.
# ---------------------------------------------------------------------------

_COMPANY_MATCH_CHECK_PROMPT = """You are checking whether a company profile matches
a sales situation.

Company Profile:
{company_context}

Product being sold: {product}
Sales situation: {situation}

Answer these questions:
1. Does this company appear to buy, use, or procure this type of product directly?
2. Is this company primarily a services business (engineering services,
   construction, project management, consulting) while the product is a
   manufactured component?
3. Do the buyer personas mentioned in the situation (Quality team, Purchase
   Manager, RFQ) match the type of company shown in the profile?
4. Is the company's revenue primarily from delivering services and
   projects, or from manufacturing and selling products? If services, the
   match is likely FALSE for a manufactured component sale.
5. If the website describes manufacturing components "to customer
   specification/drawing," as job-work, or as contract/OEM manufacturing,
   is the product being sold something they would plausibly need to
   SOURCE as an input to fulfill their own customers' orders, rather than
   something they sell as their own standardized product line?

STRICT RULES FOR YOUR DECISION:

Rule 1 — Services companies do not match.
If the company's primary business is providing services (engineering
services, construction, project management, offshore/marine services,
consulting, inspection) then the match is FALSE, regardless of what
products they mention or what alliance partners they have.

Rule 2 — Partner and alliance relationships do not count.
If the company works with, partners with, or has an alliance with a
supplier of the product being sold, that does NOT mean the company itself
procures that product. Do not use any third-party relationship as
evidence of a direct match.

Rule 3 — Buyer personas must match directly.
Compare the personas described in the sales situation (Quality team,
Purchase Manager, RFQ process) against the personas the company's website
suggests. If the website shows a fundamentally different type of buyer
(project managers, service delivery teams, consultants) with no evidence
of a manufacturing procurement function, the match is FALSE.

Rule 4 — When in doubt, return false.
It is better to ask the user to confirm the website than to generate a
strategy based on the wrong company.

Rule 5 — Job-shop / contract manufacturers are not automatically
competitors. A company that performs precision machining, job-work, or
contract/OEM manufacturing "to customer specification" is a plausible
BUYER of raw materials, semi-finished parts, or components -- even ones
resembling or overlapping with the product being sold -- because it needs
inputs to fulfill ITS OWN customers' orders. Do NOT conclude non-match
just because the website mentions producing or machining similar items.
Only conclude non-match on this basis if the website clearly markets the
EXACT product as the company's own standardized product line, sold
directly to end customers under their own brand -- not work performed to
a customer's drawing/specification. When genuinely unclear which of these
it is, this rule alone should not force a non-match verdict -- weigh it
alongside Rules 1-3 rather than treating "they mentioned making something
similar" as decisive on its own.

Work through each rule below as its own field in the JSON object, then give
your final verdict. Respond with JSON only — no text before or after the
JSON object:
{{
  "rule_1_services_check": "one sentence: is the company primarily a services business (engineering, construction, project management, offshore/marine, consulting, inspection)? if so, that alone makes this a non-match",
  "rule_2_partner_check": "one sentence: is any hydraulic/component evidence only an alliance partner or third-party relationship rather than the company's own business?",
  "rule_3_persona_check": "one sentence: do the buyer personas in the situation (Quality team, Purchase Manager, RFQ process) match what the company's website shows, or does the website show project managers/offshore engineers/service delivery teams instead?",
  "rule_5_jobshop_check": "one sentence: does the website describe this as job-work/contract manufacturing to customer specification (a plausible buyer needing inputs) rather than a standardized product line the company sells under its own brand (a genuine competitor)?",
  "match": true or false,
  "confidence": "high" or "low",
  "reason": "one sentence citing which rule determined the outcome"
}}

Rule 4 — when uncertain after the checks above, match must be false. Never
return match=true with confidence=low.
"""


async def check_company_situation_match(
    company_context: str, product: str, situation: str, website_url: str
) -> dict:
    """Separate, narrowly-scoped LLM call deciding whether the scraped
    company profile plausibly matches the product/situation. website_url
    is accepted for signature symmetry with the caller (used to build the
    refusal message in chat.py) but is not itself interpolated into the
    prompt -- the model only reasons over company_context. Fails open
    (match=True) on any API/parse error so a transient hiccup never
    silently blocks a legitimate strategy request.
    """
    prompt = _COMPANY_MATCH_CHECK_PROMPT.format(
        company_context=company_context, product=product, situation=situation
    )
    try:
        try:
            response = await _openai_generate(
                call_name="check_company_situation_match",
                system_instruction=None,
                user_content=prompt,
                json_mode=True,
                # Confirmed via live testing (on the prior Gemini model): 800
                # was too low once thinking is on at all -- thinking tokens
                # counted against the same output budget, truncating the JSON
                # mid-string before a verdict was ever reached. Kept at 2000
                # here since this rule set benefits from genuine reasoning over
                # the five checks below -- GPT-4.1-mini doesn't have a
                # separate reasoning-effort knob, so max_output_tokens is the
                # only budget to size for that.
                max_output_tokens=2000,
            )
        except OpenAIAPIError as exc:
            if not _is_openai_quota_exhausted(exc):
                raise
            _logger.warning(
                "OpenAI quota exhausted -- falling back to Gemini for check_company_situation_match"
            )
            response = await _gemini_generate(
                call_name="check_company_situation_match",
                system_instruction=None,
                user_content=prompt,
                json_mode=True,
                max_output_tokens=2000,
            )
        parsed = _extract_json(response.text)

        # If model is uncertain, force a refusal
        # Rule 4: when in doubt, match=false
        if parsed.get("confidence") == "low":
            parsed["match"] = False
            if not parsed.get("reason"):
                parsed["reason"] = (
                    "Low confidence — defaulting to "
                    "mismatch per Rule 4. Please confirm "
                    "the website is correct."
                )

        return {
            "match": bool(parsed.get("match", True)),
            "confidence": parsed.get("confidence", "low"),
            "reason": parsed.get("reason", ""),
            "rule_1": parsed.get("rule_1_services_check", ""),
            "rule_2": parsed.get("rule_2_partner_check", ""),
            "rule_3": parsed.get("rule_3_persona_check", ""),
            "rule_5": parsed.get("rule_5_jobshop_check", ""),
        }
    except Exception as exc:
        _logger.warning(
            "check_company_situation_match failed; defaulting to match=True: %s: %s",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return {
            "match": True,
            "confidence": "low",
            "reason": "",
            "exception": f"{type(exc).__name__}: {str(exc)}",
        }


# ---------------------------------------------------------------------------
# Pitch Generator — single non-streaming call producing a ready-to-send
# pitch (WhatsApp/cold-call/email) from an already-completed strategy turn.
# Deliberately NOT streamed (unlike generate_narrative_strategy): used only
# by the non-streaming post_strategy() endpoint, which needs a complete
# response object, not deltas.
# ---------------------------------------------------------------------------


async def generate_pitch(
    context: dict, conversation_history: list[dict] | None = None, output_format: str = "all_formats"
) -> str:
    """context keys: company_name, product, situation, persona,
    website_summary, previous_strategy, previous_interaction,
    sections_to_generate, conversation_summary, knowledge_context -- see
    PITCH_GENERATION_PROMPT in app/services/prompts.py. Callers must fill
    every key with a safe fallback string (never leave a key missing/None)
    before calling this -- see _build_pitch_context() in chat.py.

    conversation_history, if given, is a chronological list of
    {"role": "user"|"assistant", "content": str} turns inserted before the
    final pitch-instruction message, giving the model the actual recent
    exchanges in this conversation rather than only the previous_strategy/
    previous_interaction/conversation_summary snapshots already in context.

    output_format selects the max_output_tokens budget via
    _PITCH_MAX_OUTPUT_TOKENS -- "sales_pitch" (the 9-part document) needs
    materially more room than the other, shorter formats.

    User-facing and not backgrounded, so this follows generate_strategy()'s
    bounded-retry pattern (2 attempts covering transient API errors) rather
    than check_company_situation_match()'s fail-open pattern: a pitch that
    silently degrades to a wrong/empty message is worse than a clear error
    the frontend can surface and let the user retry.
    """
    prompt = PITCH_GENERATION_PROMPT.format(**context)
    messages = _to_openai_messages(conversation_history, prompt)
    # 2500 was tuned for the worst case among the short formats
    # (all_formats: WhatsApp + cold call + email together). "sales_pitch"
    # (9 sections) gets its own, larger budget via _PITCH_MAX_OUTPUT_TOKENS.
    max_output_tokens = _PITCH_MAX_OUTPUT_TOKENS.get(
        output_format, _DEFAULT_PITCH_MAX_OUTPUT_TOKENS
    )

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            try:
                response = await _openai_client.chat.completions.create(
                    model=_MODEL_OPENAI,
                    messages=messages,
                    max_tokens=max_output_tokens,
                    temperature=_PITCH_TEMPERATURE,
                )
                if response.usage:
                    record_usage(
                        "openai", _MODEL_OPENAI, "generate_pitch",
                        response.usage.prompt_tokens, response.usage.completion_tokens,
                    )
                content = (response.choices[0].message.content or "").strip()
            except OpenAIAPIError as exc:
                if not _is_openai_quota_exhausted(exc):
                    raise
                _logger.warning("OpenAI quota exhausted -- falling back to Gemini for generate_pitch")
                gemini_response = await _gemini_generate_from_messages(
                    call_name="generate_pitch",
                    messages=messages,
                    max_output_tokens=max_output_tokens,
                    temperature=_PITCH_TEMPERATURE,
                )
                content = gemini_response.text.strip()
            if content:
                return content
            last_error = RuntimeError("generate_pitch got an empty completion")
            _logger.warning(
                "generate_pitch attempt %d/%d produced empty content: %s",
                attempt + 1,
                2,
                last_error,
            )
        except OpenAIAPIError as exc:
            last_error = exc
            _logger.warning(
                "generate_pitch attempt %d/%d failed: %s", attempt + 1, 2, exc
            )
    assert last_error is not None
    raise last_error


# ---------------------------------------------------------------------------
# Pitch Evaluator (LLM-as-judge) — used synchronously by
# generate_verified_pitch() below, which blocks the response on it (see
# _finalize_pitch_nonstream / _pitch_stream_events in app/routers/chat.py).
# Audits an already-generated pitch against the W2R RAG Addendum rubric in
# PITCH_EVALUATION_PROMPT; never generates or alters pitch text itself.
# ---------------------------------------------------------------------------


async def evaluate_pitch(pitch_text: str, context: dict) -> dict:
    """context is the same pitch_context dict passed to generate_pitch()
    (see _build_pitch_context() in chat.py) -- output_format,
    sections_to_generate, product, situation, persona, sales_stage, and
    website_summary are used here, so the judge knows which sections were
    actually requested, what situation the pitch was meant to address,
    where the opportunity currently stands (for the problem/trigger/
    application/persona/ICP priority-ladder rule), and -- critically --
    what is actually confirmed about the PROSPECT's business, so a claim
    about the prospect that isn't backed by this can be told apart from a
    genuinely confirmed one (see no_fabricated_claims in
    PITCH_EVALUATION_PROMPT; previously this field wasn't passed at all,
    so the judge had no way to catch a plausible-sounding but unconfirmed
    claim about the prospect).

    Returns a dict shaped like:
    {"rules": [{"id": str, "status": "pass"|"fail"|"n/a", "reason": str}],
     "overall_score": int, "top_gaps": [str]}
    On any parse/API failure, returns an empty dict -- generate_verified_pitch()
    and _persist_pitch_message() (chat.py) both treat that as "no report to
    persist" rather than raising, so a broken judge never blocks a pitch.
    """
    prompt = PITCH_EVALUATION_PROMPT.format(
        pitch_text=pitch_text,
        output_format=context.get("output_format", "all_formats"),
        sections_requested=context.get("sections_to_generate", "(unknown)"),
        product=context.get("product", "(not specified)"),
        situation=context.get("situation", "(not specified)"),
        persona=context.get("persona", "unknown"),
        sales_stage=context.get("sales_stage", "unknown"),
        website_summary=context.get(
            "website_summary", "(no website analysis available)"
        ),
    )
    try:
        response = await _client.aio.models.generate_content(
            model=_MODEL_GEMINI,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                thinking_config=_THINKING_MINIMAL,
            ),
        )
        _record_gemini_usage(response, "evaluate_pitch")
        return _extract_json(response.text)
    except Exception as exc:
        _logger.warning("evaluate_pitch failed: %s", exc)
        return {}


# Below this score (out of 100), generate_verified_pitch() makes its one
# automatic retry, feeding the specific failed rules back to the model.
# Raised from 70 to 90: even a single missed rule (e.g. one unconfirmed
# claim slipping past no_fabricated_claims) should trigger the retry now,
# rather than being absorbed as an acceptable minor gap.
_PITCH_COMPLIANCE_THRESHOLD = 90


def _pitch_score(eval_result: dict) -> int | None:
    """None means the judge call itself failed or returned an unusable
    shape -- callers must treat that as "unknown", never as a failing
    score, so a broken judge can't force an unnecessary retry."""
    score = eval_result.get("overall_score") if eval_result else None
    return score if isinstance(score, int) else None


def _format_compliance_feedback(eval_result: dict) -> str:
    """Turns evaluate_pitch()'s "fail"-status rules into the
    {compliance_feedback} block PITCH_GENERATION_PROMPT injects on
    generate_verified_pitch()'s retry attempt -- lists exactly what to fix
    instead of asking for a generic rewrite."""
    failed = [
        rule for rule in eval_result.get("rules", [])
        if isinstance(rule, dict) and rule.get("status") == "fail"
    ]
    if not failed:
        return ""
    issues = "\n".join(
        f"- {rule.get('id', 'unknown_rule')}: {rule.get('reason', '')}" for rule in failed
    )
    return f"""==================================================
COMPLIANCE FIX REQUIRED
==================================================

Your previous draft of this pitch did not meet the required sales pitch
framework. Specifically:

{issues}

Rewrite the pitch to fix ONLY these issues. Keep everything else that
already worked -- this is a targeted correction, not a full rewrite from
scratch.

"""


async def generate_verified_pitch(
    context: dict,
    conversation_history: list[dict] | None = None,
    output_format: str = "all_formats",
    score_threshold: int = _PITCH_COMPLIANCE_THRESHOLD,
) -> tuple[str, dict | None]:
    """Blocking wrapper around generate_pitch() + evaluate_pitch(): judges
    the first draft against the W2R rubric and, if it scores below
    score_threshold, makes exactly one regeneration attempt with the
    specific failed rules fed back via {compliance_feedback}, then returns
    whichever attempt scored higher. Bounded at 2 generate_pitch() calls +
    2 evaluate_pitch() calls worst case.

    Returns (pitch_text, eval_result) where eval_result is the winning
    attempt's evaluate_pitch() dict, or None if the judge itself never
    produced a usable result (fail-open -- the pitch is still returned
    normally; see _pitch_score())."""
    pitch_v1 = await generate_pitch(context, conversation_history, output_format)
    eval_v1 = await evaluate_pitch(pitch_v1, context)
    score_v1 = _pitch_score(eval_v1)

    if score_v1 is None or score_v1 >= score_threshold:
        return pitch_v1, (eval_v1 or None)

    context_v2 = {**context, "compliance_feedback": _format_compliance_feedback(eval_v1)}
    pitch_v2 = await generate_pitch(context_v2, conversation_history, output_format)
    eval_v2 = await evaluate_pitch(pitch_v2, context)
    score_v2 = _pitch_score(eval_v2)

    if score_v2 is not None and score_v2 >= score_v1:
        return pitch_v2, eval_v2
    return pitch_v1, eval_v1


# ---------------------------------------------------------------------------
# Conversation Memory Summarizer — background-task only (see
# _summarize_and_store_memory in app/routers/chat.py). Best-effort: callers
# should swallow exceptions rather than surface them to the user.
# ---------------------------------------------------------------------------


async def summarize_conversation_memory(
    existing_summary: str | None,
    recent_turns: list[tuple[str, str]],
) -> str:
    """recent_turns is a list of (sender, content) pairs in chronological
    order. Folds them into a rolling, bounded-length summary."""
    turns_text = "\n".join(f"{sender.upper()}: {content[:500]}" for sender, content in recent_turns)
    prompt = CONVERSATION_MEMORY_PROMPT.format(
        existing_summary=existing_summary or "(none yet)",
        recent_turns=turns_text or "(no messages)",
    )
    response = await _client.aio.models.generate_content(
        model=_MODEL_GEMINI,
        contents=prompt,
        config=types.GenerateContentConfig(
            max_output_tokens=300,
            thinking_config=_THINKING_MINIMAL,
        ),
    )
    _record_gemini_usage(response, "summarize_conversation_memory")
    return response.text.strip()


# ---------------------------------------------------------------------------
# DEPRECATED — use generate_strategy() instead
# generate_answer() kept temporarily so old endpoint does not break
# Remove once /chat/conversations/{id}/messages is retired
# ---------------------------------------------------------------------------

async def generate_answer(question: str, context_entries: list[KnowledgeEntry]) -> str:
    context = _format_context(context_entries)
    system_prompt = """
You are a Senior B2B Industrial Sales Director with 20 years of experience
in industrial and manufacturing sales in India.

IMPORTANT: This is a legacy endpoint. For best results use the
/strategy endpoint which requires company website, product, and situation.

If the user has not provided a specific company, product, and situation —
respond with exactly this:

"To give you useful advice, I need 3 things:
1. The website of the company you are selling to
2. The product or service you are selling
3. The specific challenge you are facing right now

Example: My customer is ABC Engineering (www.abceng.com).
I am selling hydraulic manifold blocks.
The purchase manager says they already have two approved vendors."

If they have provided all three, give specific advice in this structure:

**SITUATION SUMMARY**
**WHAT IS PROBABLY GOING ON**
**YOUR OBJECTIVE RIGHT NOW**
**RECOMMENDED STRATEGY**
**WHO TO APPROACH**
**QUESTIONS TO ASK**
**WHAT TO SAY**
**EMAIL OR WHATSAPP DRAFT**
**WHAT NOT TO DO**
**NEXT ACTION**
**KNOWLEDGE USED**
"""
    response = await _client.aio.models.generate_content(
        model=_MODEL_GEMINI,
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=f"{system_prompt}\n\nContext:\n{context}",
            thinking_config=_THINKING_MINIMAL,
        ),
    )
    _record_gemini_usage(response, "generate_answer")
    return response.text
