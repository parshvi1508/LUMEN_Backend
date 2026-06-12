from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from crm_api import db
from crm_api.config import get_settings
from crm_api.routers.campaigns import router as campaigns_router
from crm_api.routers.ingest import router as ingest_router
from crm_api.routers.receipts import router as receipts_router
from crm_api.routers.segments import router as segments_router


class HealthResponse(BaseModel):
    status: str
    db: bool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await db.ping()
    app.state.http_client = httpx.AsyncClient(timeout=10.0)
    yield
    await app.state.http_client.aclose()


app = FastAPI(title=get_settings().app_name, lifespan=lifespan)
app.include_router(ingest_router)
app.include_router(segments_router)
app.include_router(campaigns_router)
app.include_router(receipts_router)


async def health() -> JSONResponse:
    try:
        db_ok = await db.ping()
    except Exception:
        db_ok = False
    body = HealthResponse(status="ok" if db_ok else "degraded", db=db_ok)
    return JSONResponse(content=body.model_dump(), status_code=200 if db_ok else 503)


app.add_api_route("/health", health, methods=["GET"], response_model=HealthResponse)
app.add_api_route(
    "/healthz", health, methods=["GET"], response_model=HealthResponse, include_in_schema=False
)
