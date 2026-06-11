from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from crm_api import db
from crm_api.config import get_settings


class HealthResponse(BaseModel):
    status: str
    db: bool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await db.ping()
    yield


app = FastAPI(title=get_settings().app_name, lifespan=lifespan)


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
