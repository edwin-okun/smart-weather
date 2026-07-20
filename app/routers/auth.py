import base64
import binascii
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, unquote_plus, urlsplit, urlunsplit

from fastapi import APIRouter, Form, Header, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from app.schemas.auth import TokenResponse
from app.services.auth import (
    create_authorization_code_redirect,
    issue_authorization_code_token,
    issue_client_credentials_token,
)

router = APIRouter(tags=["auth"])


@router.get(
    "/authorize",
    operation_id="authorize_oauth_client",
    summary="Authorize an OAuth client",
    description=(
        "Issues a short-lived authorization code for registered clients using "
        "the authorization-code flow with PKCE."
    ),
)
async def authorize(
    client_id: Annotated[str, Query()],
    response_type: Annotated[str, Query()],
    redirect_uri: Annotated[str, Query()],
    code_challenge: Annotated[str, Query()],
    code_challenge_method: Annotated[str, Query()],
    scope: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
):
    """Validate an OAuth authorization request and redirect with a one-time code."""
    code, _ = await create_authorization_code_redirect(
        client_id=client_id,
        response_type=response_type,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
    )
    return RedirectResponse(
        url=_add_query_params(redirect_uri, {"code": code, "state": state}),
        status_code=status.HTTP_302_FOUND,
    )


@router.get(
    "/.well-known/oauth-authorization-server",
    operation_id="oauth_authorization_server_metadata",
    summary="OAuth authorization server metadata",
    include_in_schema=False,
)
async def oauth_authorization_server_metadata(request: Request):
    """Expose OAuth metadata used by dynamic authorization clients."""
    base_url = str(request.base_url).rstrip("/")
    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "client_credentials"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "client_secret_post",
            "none",
        ],
        "scopes_supported": ["weather:read", "weather:history:read"],
    }


@router.post(
    "/oauth/token",
    response_model=TokenResponse,
    operation_id="issue_oauth_token",
    summary="Issue an OAuth access token",
    description="Issues short-lived bearer tokens using the client credentials grant.",
)
async def issue_token(
    grant_type: Annotated[str, Form()],
    scope: Annotated[str | None, Form()] = None,
    client_id: Annotated[str | None, Form()] = None,
    client_secret: Annotated[str | None, Form()] = None,
    code: Annotated[str | None, Form()] = None,
    redirect_uri: Annotated[str | None, Form()] = None,
    code_verifier: Annotated[str | None, Form()] = None,
    authorization: Annotated[str | None, Header()] = None,
):
    """Issue access tokens for client-credentials and PKCE authorization-code grants."""
    basic_client_id, basic_client_secret = _parse_basic_auth(authorization)

    if grant_type == "client_credentials":
        client_id, client_secret = _resolve_client_secret_credentials(
            body_client_id=client_id,
            body_client_secret=client_secret,
            basic_client_id=basic_client_id,
            basic_client_secret=basic_client_secret,
        )
        return await issue_client_credentials_token(
            grant_type=grant_type,
            client_id=client_id,
            client_secret=client_secret,
            scope=scope,
        )

    if grant_type == "authorization_code":
        resolved_client_id = _resolve_public_client_id(
            body_client_id=client_id,
            basic_client_id=basic_client_id,
        )
        if not code or not redirect_uri or not code_verifier:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid_request",
            )

        return await issue_authorization_code_token(
            grant_type=grant_type,
            client_id=resolved_client_id,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="unsupported_grant_type",
    )


def _parse_basic_auth(authorization: str | None) -> tuple[str | None, str | None]:
    """Return OAuth client credentials from a Basic auth header, if present."""
    if authorization is None:
        return None, None

    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "basic":
        return None, None

    try:
        decoded = base64.b64decode(credentials, validate=True).decode("utf-8")
        client_id, client_secret = decoded.split(":", 1)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_client",
            headers={"WWW-Authenticate": "Basic"},
        ) from None

    return unquote_plus(client_id), unquote_plus(client_secret)


def _resolve_client_secret_credentials(
    *,
    body_client_id: str | None,
    body_client_secret: str | None,
    basic_client_id: str | None,
    basic_client_secret: str | None,
) -> tuple[str, str]:
    """Resolve confidential-client credentials from either Basic auth or form fields."""
    has_body_credentials = body_client_id is not None or body_client_secret is not None
    has_basic_credentials = basic_client_id is not None or basic_client_secret is not None

    if has_body_credentials and has_basic_credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_request",
        )

    client_id = basic_client_id if has_basic_credentials else body_client_id
    client_secret = (
        basic_client_secret if has_basic_credentials else body_client_secret
    )

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_client",
            headers={"WWW-Authenticate": "Basic"},
        )

    return client_id, client_secret


def _resolve_public_client_id(
    *,
    body_client_id: str | None,
    basic_client_id: str | None,
) -> str:
    """Resolve the client ID for PKCE public clients without requiring a secret."""
    if body_client_id and basic_client_id and body_client_id != basic_client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_request",
        )

    client_id = body_client_id or basic_client_id
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_request",
        )

    return client_id


def _add_query_params(url: str, params: dict[str, str | None]) -> str:
    """Append query parameters to a redirect URI while preserving existing params."""
    parsed = urlsplit(url)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    query_items.extend((key, value) for key, value in params.items() if value is not None)
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query_items),
            parsed.fragment,
        )
    )
