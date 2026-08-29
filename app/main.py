from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db.session import engine
from app.routers import admin, auth, bd_chat, chat
from app.services.embeddings import warm_up_model


@asynccontextmanager
async def lifespan(app: FastAPI):
    warm_up_model()
    yield
    await engine.dispose()


app = FastAPI(title="MOTM AI Sales Director — Auth Service", lifespan=lifespan)

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
