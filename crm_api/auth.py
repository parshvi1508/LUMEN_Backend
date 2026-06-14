from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from crm_api.config import get_settings

SUPABASE_AUDIENCE = "authenticated"

_bearer = HTTPBearer(auto_error=False)
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]

# Two verification modes, chosen by config:
#   - JWKS (ES256): Supabase signs access tokens with asymmetric keys; we fetch the
#     public key set once and let PyJWKClient pick the key by the token's "kid".
#   - HS256 fallback: legacy shared JWT secret. Used by tests and local dev.
# Newer Supabase projects default to ES256, so JWKS is the production path.
_settings = get_settings()
_jwks_client = (
    PyJWKClient(_settings.supabase_jwks_url) if _settings.supabase_jwks_url else None
)


async def require_user(creds: BearerDep) -> dict:
    if creds is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = creds.credentials
    try:
        if _jwks_client is not None:
            # NOTE: get_signing_key_from_jwt does a (cached) network fetch on first
            # use. At demo scale this is fine; PyJWKClient caches keys thereafter.
            signing_key = _jwks_client.get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=["ES256"],
                audience=SUPABASE_AUDIENCE,
            )
        secret = _settings.supabase_jwt_secret
        if secret is None:
            raise HTTPException(status_code=500, detail="auth not configured")
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=SUPABASE_AUDIENCE,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
