from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.schemas.auth import AuthenticatedClient
from app.services.auth import authenticate_bearer_token


bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_client(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthenticatedClient:
    base_url = (settings.public_base_url or str(request.base_url)).rstrip("/")
    resource_metadata = f"{base_url}/.well-known/oauth-protected-resource/mcp"
    challenge = f'Bearer resource_metadata="{resource_metadata}"'
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_token",
            headers={"WWW-Authenticate": challenge},
        )

    if credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_token_type",
            headers={"WWW-Authenticate": challenge},
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
