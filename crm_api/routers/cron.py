import os

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

router = APIRouter(prefix="/internal", tags=["cron"])


@router.post("/process-jobs")
async def process_jobs(request: Request, x_cron_token: str = Header(None)):
    if x_cron_token != os.getenv("CRON_TOKEN"):
        raise HTTPException(status_code=401, detail="invalid cron token")

    try:
        base_url = f"{request.url.scheme}://{request.url.netloc}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.get(f"{base_url}/health")
    except Exception as e:
        return {"success": False, "error": str(e)}

    return {"success": True}
