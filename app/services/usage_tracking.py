"""Per-request LLM token/cost accounting.

record_usage() is called from every LLM call site in app/services/llm.py.
Records accumulate in a ContextVar scoped to the current request (see
start_usage_tracking() in app/routers/chat.py) and are flushed to the
llm_call_logs table once the request's assistant message is persisted.
"""

from contextvars import ContextVar
from dataclasses import dataclass

# $/token, derived from the published $/1M rates confirmed against the
# official pricing pages on 2026-08-25:
#   gpt-4.1-mini: https://developers.openai.com/api/docs/pricing
#     ($0.40 / 1M input, $1.60 / 1M output)
#   gemini-3.1-flash-lite: https://ai.google.dev/gemini-api/docs/pricing
#     ($0.25 / 1M input, $1.50 / 1M output, text/image/video tier)
# Re-check these if either model's pricing page changes.
_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4.1-mini": (0.40 / 1_000_000, 1.60 / 1_000_000),
    "gemini-3.1-flash-lite": (0.25 / 1_000_000, 1.50 / 1_000_000),
}


@dataclass
class UsageRecord:
    provider: str
    model: str
    call_name: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float


_usage_ctx: ContextVar[list[UsageRecord] | None] = ContextVar("_usage_ctx", default=None)


def start_usage_tracking() -> None:
    """Resets the current request's usage-record accumulator to empty.
    Call once at the top of each chat.py endpoint that runs the LLM
    pipeline. record_usage() calls made anywhere afterwards during the same
    request -- including inside a StreamingResponse's async generator body,
    which runs later but in the same asyncio Task -- append to this same
    list via the ContextVar.

    Deliberately has no matching "end" call: FastAPI/Starlette handles each
    HTTP request in its own asyncio Task, so this mutation never leaks into
    a different request's task -- an explicit reset would in fact break the
    streaming endpoints, where the generator (and its LLM calls) only runs
    *after* the endpoint function that would perform the reset has already
    returned."""
    _usage_ctx.set([])


def record_usage(provider: str, model: str, call_name: str, input_tokens: int, output_tokens: int) -> None:
    """No-ops outside a usage_tracking_session() -- safe to call
    unconditionally from llm.py regardless of caller."""
    records = _usage_ctx.get()
    if records is None:
        return
    input_price, output_price = _PRICING[model]
    cost_usd = input_tokens * input_price + output_tokens * output_price
    records.append(
        UsageRecord(
            provider=provider,
            model=model,
            call_name=call_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_usd=cost_usd,
        )
    )


def get_recorded_usage() -> list[UsageRecord]:
    """Returns everything recorded so far in this request AND clears the
    accumulator. Some request paths persist usage more than once per
    request (e.g. a grounding "seed" message committed first, then the
    real pitch message committed afterward) -- draining here (rather than
    just reading) means each flush only contains calls made since the
    previous flush, so the same LLM call is never written to
    llm_call_logs twice."""
    records = _usage_ctx.get()
    if records is None:
        return []
    _usage_ctx.set([])
    return records
