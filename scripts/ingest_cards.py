"""Load a JSONL file of pre-written knowledge cards into knowledge_entries.

Each line is a JSON object with the sales-principle-card schema, e.g.:
    {"principle_id": "INF-0001", "principle_name": "...", "principle": "...",
     "when_to_use": "...", "recommended_action": "...", "motm_application": "...",
     "source": "...", "author": "...", "chapter": "...", "sales_stage": [...],
     "problem_types": [...], "customer_personas": [...], "tags": [...], ...}

`title` = principle_name. `content` (what gets embedded and searched) is the
principle text plus its practical-guidance fields, since a question phrased
as "how do I handle X" should match on guidance, not just theory. Every
other field is preserved as-is in `metadata` (jsonb) -- not searched today,
but available later for filtering (e.g. by sales_stage) or richer citations.

No chunking is performed -- each line is already sized to become exactly one
row.

Usage:
    python -m scripts.ingest_cards path/to/cards.jsonl
    python -m scripts.ingest_cards path/to/cards.jsonl --persona shared --type case_card
"""

import argparse
import asyncio
import json
from pathlib import Path

from app.db.models import KnowledgeEntry
from app.db.session import async_session_factory
from app.models.schemas import KnowledgePersona, KnowledgeType
from app.services.embeddings import embed_texts

_INSERT_BATCH_SIZE = 32

# Fields folded into the embedded/searched content, in this order. A missing
# field is simply skipped, not an error -- cards aren't guaranteed uniform.
_CONTENT_FIELDS = [
    ("Principle", "principle"),
    ("When to use", "when_to_use"),
    ("Recommended action", "recommended_action"),
    ("MOTM application", "motm_application"),
]


def _build_content(card: dict) -> str:
    sections = [f"{label}: {card[field]}" for label, field in _CONTENT_FIELDS if card.get(field)]
    return "\n\n".join(sections)


def _read_cards(path: Path) -> list[dict]:
    cards = []
    with path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            card = json.loads(line)
            if "principle" not in card:
                raise ValueError(f"{path}:{line_number} missing 'principle'")
            if "principle_name" not in card:
                if "principle_id" not in card:
                    raise ValueError(
                        f"{path}:{line_number} missing 'principle_name' (and no 'principle_id' to fall back on)"
                    )
                card["principle_name"] = card["principle_id"]
            cards.append(card)
    return cards


async def _ingest(path: Path, persona: KnowledgePersona, type_: KnowledgeType) -> None:
    cards = _read_cards(path)
    print(f"Read {len(cards)} cards from {path}")

    async with async_session_factory() as session:
        for batch_start in range(0, len(cards), _INSERT_BATCH_SIZE):
            batch = cards[batch_start : batch_start + _INSERT_BATCH_SIZE]
            contents = [_build_content(card) for card in batch]
            embeddings = embed_texts(contents)

            session.add_all(
                KnowledgeEntry(
                    persona=persona,
                    type=type_,
                    title=card["principle_name"],
                    content=content,
                    metadata_=card,
                    embedding=embedding,
                )
                for card, content, embedding in zip(batch, contents, embeddings)
            )
            await session.commit()
            print(f"  ingested {min(batch_start + len(batch), len(cards))}/{len(cards)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl_path", type=Path)
    parser.add_argument(
        "--persona",
        choices=[p.value for p in KnowledgePersona],
        default=KnowledgePersona.sell_motm.value,
    )
    parser.add_argument(
        "--type",
        choices=[t.value for t in KnowledgeType],
        default=KnowledgeType.principle_card.value,
    )
    args = parser.parse_args()

    asyncio.run(
        _ingest(args.jsonl_path, KnowledgePersona(args.persona), KnowledgeType(args.type))
    )


if __name__ == "__main__":
    main()
