import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.db.session import engine
from app.logging_config import configure_logging
from app.routers import admin, auth, bd_chat, chat
from app.services.embeddings import warm_up_model

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    warm_up_model()
    yield
    await engine.dispose()


app = FastAPI(title="MOTM AI Sales Director — Auth Service", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def _log_http_exception(request, exc: HTTPException):
    """Adds visibility into handled errors (bad login, 409 conflicts, etc.)
    without changing the response FastAPI would already send."""
    logger.warning(
        "HTTP %s on %s %s: %s", exc.status_code, request.method, request.url.path, exc.detail
    )
    return await http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def _log_unhandled_exception(request, exc: Exception):
    """Safety net for bugs that escape a route handler. Does not affect the
    'log and continue' pattern used by chat.py/bd_chat.py's background
    tasks, since those run via BackgroundTasks after the response is
    already sent -- outside this handler's request/response cycle."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

# Allows the local Vite dev server to call this API from a different origin.
# Vite picks the next free port (5173, 5174, ...) if the default is taken, so
# match any localhost/127.0.0.1 port rather than a single hardcoded one.
# Bearer-token auth (not cookies), so allow_credentials stays False.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin] if settings.frontend_origin else [],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(chat.router)
app.include_router(bd_chat.router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
