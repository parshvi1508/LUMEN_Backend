import hashlib
import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.config import get_settings
from crm_api.db import get_session
from crm_api.schemas.receipts import ReceiptBatch, ReceiptResponse
from crm_api.services import receipt_service

router = APIRouter(prefix="/api/v1/receipts", tags=["receipts"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=ReceiptResponse)
async def receipts(request: Request, session: SessionDep) -> ReceiptResponse:
    body = await request.body()
    signature = request.headers.get("X-Signature")
    expected = hmac.new(
        get_settings().channel_hmac_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    if signature is None or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        batch = ReceiptBatch.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    return await receipt_service.process_batch(session, batch)
