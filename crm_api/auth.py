from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from crm_api.config import get_settings

SUPABASE_AUDIENCE = "authenticated"

_bearer = HTTPBearer(auto_error=False)
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


async def require_user(creds: BearerDep) -> dict:
    if creds is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        return jwt.decode(
            creds.credentials,
            get_settings().supabase_jwt_secret,
            algorithms=["HS256"],
            audience=SUPABASE_AUDIENCE,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
