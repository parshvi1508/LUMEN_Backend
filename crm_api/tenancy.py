import uuid

from fastapi import HTTPException, Request


def require_tenant(request: Request) -> uuid.UUID:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=403, detail="no tenant in token")
    if isinstance(tenant_id, uuid.UUID):
        return tenant_id
    try:
        return uuid.UUID(str(tenant_id))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="malformed tenant id") from exc
