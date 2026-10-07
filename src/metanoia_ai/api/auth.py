"""JWT check for tokens issued by the Backend."""

from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from metanoia_ai.config import Settings, get_settings

# The Backend signs with HS256 only. Never accept any other algorithm.
ALGORITHM = "HS256"
REQUIRED_CLAIMS = ["exp", "iss", "aud", "sub"]


class AuthError(Exception):
    """The token is invalid: bad signature, expired, wrong issuer/audience or user ID."""


def decode_user_id(token: str, settings: Settings) -> UUID:
    """Verify the token and return the user ID from the `sub` claim.

    The key is used as a plain UTF-8 string, like the Backend
    (`Encoding.UTF8.GetBytes`), not base64-decoded.
    """
    try:
        payload = jwt.decode(
            token,
            key=settings.jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            leeway=settings.jwt_leeway_seconds,
            options={"require": REQUIRED_CLAIMS},
        )
        return UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError) as error:
        raise AuthError(str(error)) from error


# auto_error=False: we return our own 401 for every failure, including a missing header.
bearer_scheme = HTTPBearer(auto_error=False)


def unauthorized() -> HTTPException:
    """401 response. The reason is not shown, so callers cannot probe the check."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UUID:
    """FastAPI dependency: read `Authorization: Bearer <token>` and return the user ID."""
    if credentials is None:
        raise unauthorized()
    try:
        return decode_user_id(credentials.credentials, settings)
    except AuthError as error:
        raise unauthorized() from error
