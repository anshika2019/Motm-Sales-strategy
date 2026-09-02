import logging

from app.config import settings


def configure_logging() -> None:
    """Central logging setup, called once at app startup (see lifespan() in
    main.py). Uses force=True because uvicorn installs its own root handlers
    before app code runs, which would otherwise make a plain basicConfig()
    call a silent no-op."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s [%(filename)s:%(lineno)d] %(message)s",
        force=True,
    )
    # Third-party HTTP client libs are chatty at INFO; keep app logs readable.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
