# Backend (FastAPI). Built from the repo root so it can COPY app/ + requirements.txt
# without needing them duplicated anywhere.
FROM python:3.12-slim

# sentence-transformers pulls in torch, which needs a C++ runtime at import
# time even though no compilation happens here (wheels are prebuilt).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
# Install the CPU-only torch build first: sentence-transformers otherwise
# pulls in torch's default GPU/CUDA wheels (several GB of libraries this
# VPS, with no GPU, will never use).
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /home/appuser/.cache/huggingface \
    && chown -R appuser:appuser /home/appuser /srv
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

# --workers 1: sentence-transformers' bge-m3 model (~2GB) is loaded once per
# worker process at startup (see app/services/embeddings.py warm_up_model).
# Multiple workers would multiply that memory cost; scale via multiple
# container replicas behind the reverse proxy instead if throughput demands it.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
