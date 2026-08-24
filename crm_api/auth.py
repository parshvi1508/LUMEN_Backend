import asyncio
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from crm_api.config import get_settings

SUPABASE_AUDIENCE = "authenticated"

_bearer = HTTPBearer(auto_error=False)
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]

# JWKS (ES256) is the production path for modern Supabase projects; the HS256
# shared secret is the legacy fallback used by tests and local dev.
_settings = get_settings()
_jwks_client = PyJWKClient(_settings.supabase_jwks_url) if _settings.supabase_jwks_url else None


async def require_user(request: Request, creds: BearerDep) -> dict:
    if creds is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = creds.credentials
    try:
        if _jwks_client is not None:
            # get_signing_key_from_jwt does a blocking network fetch on cache miss;
            # run it off the event loop so it cannot stall other requests.
            signing_key = await asyncio.to_thread(_jwks_client.get_signing_key_from_jwt, token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["ES256"],
                audience=SUPABASE_AUDIENCE,
            )
        else:
            secret = _settings.supabase_jwt_secret
            if secret is None:
                raise HTTPException(status_code=500, detail="auth not configured")
            payload = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience=SUPABASE_AUDIENCE,
            )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
    request.state.user = payload
    request.state.tenant_id = payload.get("tenant_id") or (payload.get("app_metadata") or {}).get(
        "tenant_id"
    )
    return payload
