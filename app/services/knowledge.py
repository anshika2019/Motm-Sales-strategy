from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeEntry


async def fetch_known_problem_types(session: AsyncSession) -> list[str]:
    """Distinct problem_types values actually present across ingested
    knowledge_entries.metadata -- the classification candidate list is
    driven by real ingested data rather than a hardcoded/duplicated enum,
    so it stays in sync as new card files (with new categories) are
    ingested."""
    result = await session.execute(
        select(distinct(func.jsonb_array_elements_text(KnowledgeEntry.metadata_["problem_types"])))
    )
    return sorted(value for value in result.scalars().all() if value)
