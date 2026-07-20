from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.schemas.auth import AuthenticatedClient
from app.services.auth import authenticate_bearer_token


bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_client(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthenticatedClient:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_token_type",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await authenticate_bearer_token(credentials.credentials)


def require_scopes(*required_scopes: str) -> Callable[..., AuthenticatedClient]:
    async def dependency(
        client: Annotated[AuthenticatedClient, Depends(get_current_client)],
    ) -> AuthenticatedClient:
        missing_scopes = set(required_scopes) - client.scopes
        if missing_scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_scope",
                headers={
                    "WWW-Authenticate": (
                        'Bearer error="insufficient_scope", '
                        f'scope="{" ".join(required_scopes)}"'
                    )
                },
            )
        return client

    return dependency
