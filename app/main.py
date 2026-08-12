from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.session import create_pool
from app.routers import admin, auth


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool()
    yield
    await app.state.pool.close()


app = FastAPI(title="MOTM AI Sales Director — Auth Service", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(admin.router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
