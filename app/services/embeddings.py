from functools import lru_cache

from sentence_transformers import SentenceTransformer

# knowledge_entries.embedding is vector(1024) -- bge-m3's default dense
# output dimension, chosen so no schema change is needed. Cached with
# lru_cache so the ~2GB model is loaded once per process and reused across
# every embed_texts() call, not reloaded per call.
_MODEL_NAME = "BAAI/bge-m3"


@lru_cache
def _get_model() -> SentenceTransformer:
    return SentenceTransformer(_MODEL_NAME)


def warm_up_model() -> None:
    """Loads the model eagerly. Call once at app startup so the ~2GB
    download/load cost is paid before the first request, not during it."""
    _get_model()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of passages/queries with bge-m3. Same model and call
    path for both ingestion (passages) and query-time embedding -- bge-m3
    doesn't require different instruction prefixes for symmetric
    similarity search, unlike some other embedding models."""
    model = _get_model()
    return model.encode(texts, normalize_embeddings=True).tolist()
