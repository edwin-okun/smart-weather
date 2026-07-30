import base64
import binascii
import json
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, unquote_plus, urlsplit, urlunsplit

from fastapi import APIRouter, Form, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import ValidationError

from app.config import settings
from app.schemas.auth import (
    DynamicClientRegistrationErrorResponse,
    DynamicClientRegistrationRequest,
    DynamicClientRegistrationResponse,
    TokenResponse,
)
from app.services.auth import (
    DynamicClientRegistrationError,
    create_authorization_code_redirect,
    issue_authorization_code_token,
    issue_client_credentials_token,
    issue_refresh_token,
    register_dynamic_api_client,
)

router = APIRouter(tags=["auth"])
MAX_REGISTRATION_BODY_BYTES = 64 * 1024
REGISTRATION_REQUEST_BODY_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": DynamicClientRegistrationRequest.model_json_schema()
            }
        },
    }
}


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
    request: Request,
    client_id: Annotated[str, Query()],
    response_type: Annotated[str, Query()],
    redirect_uri: Annotated[str, Query()],
    code_challenge: Annotated[str, Query()],
    code_challenge_method: Annotated[str, Query()],
    scope: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    resource: Annotated[str | None, Query()] = None,
):
    """Validate an OAuth authorization request and redirect with a one-time code."""
    _validate_resource(request, resource)
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
    base_url = _public_base_url(request)
    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "registration_endpoint": f"{base_url}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": [
            "authorization_code",
            "refresh_token",
            "client_credentials",
        ],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "client_secret_post",
            "none",
        ],
        "scopes_supported": ["weather:read", "weather:history:read"],
    }


@router.get(
    "/.well-known/oauth-protected-resource",
    operation_id="oauth_protected_resource_metadata",
    include_in_schema=False,
)
@router.get(
    "/.well-known/oauth-protected-resource/mcp",
    operation_id="oauth_protected_resource_metadata_mcp",
    include_in_schema=False,
)
async def oauth_protected_resource_metadata(request: Request):
    """Expose RFC 9728 metadata for the protected MCP resource."""
    base_url = _public_base_url(request)
    return {
        "resource": f"{base_url}/mcp",
        "authorization_servers": [base_url],
        "scopes_supported": ["weather:read"],
        "bearer_methods_supported": ["header"],
    }


@router.post(
    "/register",
    response_model=DynamicClientRegistrationResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    operation_id="register_oauth_client",
    summary="Dynamically register an OAuth client",
    description=(
        "Registers a public or confidential OAuth client using RFC 7591 metadata."
    ),
    responses={400: {"model": DynamicClientRegistrationErrorResponse}},
    openapi_extra=REGISTRATION_REQUEST_BODY_SCHEMA,
)
async def register_client(
    request: Request,
) -> DynamicClientRegistrationResponse | JSONResponse:
    """Register a public PKCE client and return RFC 7591 client metadata."""
    content_type = request.headers.get("content-type", "").partition(";")[0]
    if content_type.strip().lower() != "application/json":
        return _registration_error(
            "invalid_client_metadata",
            "The registration request must use application/json.",
        )

    try:
        raw_registration = await _read_registration_json(request)
        if not isinstance(raw_registration, dict):
            raise ValueError
        registration = DynamicClientRegistrationRequest.model_validate(
            raw_registration
        )
    except ValidationError:
        error = (
            "invalid_redirect_uri"
            if _has_invalid_redirect_metadata(raw_registration)
            else "invalid_client_metadata"
        )
        return _registration_error(
            error,
            "The registration request contains invalid client metadata.",
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _registration_error(
            "invalid_client_metadata",
            "The registration request must be a valid JSON object.",
        )

    try:
        return await register_dynamic_api_client(registration)
    except DynamicClientRegistrationError as exc:
        return _registration_error(exc.error, exc.description)


@router.post(
    "/oauth/token",
    response_model=TokenResponse,
    response_model_exclude_none=True,
    operation_id="issue_oauth_token",
    summary="Issue an OAuth access token",
    description="Issues short-lived bearer tokens using the client credentials grant.",
)
async def issue_token(
    request: Request,
    grant_type: Annotated[str, Form()],
    scope: Annotated[str | None, Form()] = None,
    client_id: Annotated[str | None, Form()] = None,
    client_secret: Annotated[str | None, Form()] = None,
    code: Annotated[str | None, Form()] = None,
    redirect_uri: Annotated[str | None, Form()] = None,
    code_verifier: Annotated[str | None, Form()] = None,
    refresh_token: Annotated[str | None, Form()] = None,
    resource: Annotated[str | None, Form()] = None,
    authorization: Annotated[str | None, Header()] = None,
):
    """Issue access tokens for client-credentials and PKCE authorization-code grants."""
    _validate_resource(request, resource)
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
        resolved_client_id, resolved_client_secret = _resolve_token_credentials(
            body_client_id=client_id,
            body_client_secret=client_secret,
            basic_client_id=basic_client_id,
            basic_client_secret=basic_client_secret,
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
            client_secret=resolved_client_secret,
        )

    if grant_type == "refresh_token":
        resolved_client_id, resolved_client_secret = _resolve_token_credentials(
            body_client_id=client_id,
            body_client_secret=client_secret,
            basic_client_id=basic_client_id,
            basic_client_secret=basic_client_secret,
        )
        if not refresh_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid_request",
            )
        return await issue_refresh_token(
            client_id=resolved_client_id,
            client_secret=resolved_client_secret,
            refresh_token=refresh_token,
            scope=scope,
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

    return unquote_plus(client_id), unquote_plus(client_secret) or None


def _registration_error(error: str, description: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": error, "error_description": description},
    )


async def _read_registration_json(request: Request) -> object:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_REGISTRATION_BODY_BYTES:
                raise ValueError
        except ValueError:
            raise ValueError from None

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_REGISTRATION_BODY_BYTES:
            raise ValueError
    return json.loads(body)


def _has_invalid_redirect_metadata(registration: dict[str, object]) -> bool:
    redirect_uris = registration.get("redirect_uris")
    return (
        not isinstance(redirect_uris, list)
        or not 1 <= len(redirect_uris) <= 10
        or any(not isinstance(uri, str) for uri in redirect_uris)
    )


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


def _resolve_token_credentials(
    *,
    body_client_id: str | None,
    body_client_secret: str | None,
    basic_client_id: str | None,
    basic_client_secret: str | None,
) -> tuple[str, str | None]:
    """Resolve public or confidential credentials for token grants."""
    if (
        body_client_id
        and basic_client_id
        and body_client_id != basic_client_id
    ) or (body_client_secret is not None and basic_client_secret):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_request",
        )

    client_id = basic_client_id or body_client_id
    client_secret = basic_client_secret or body_client_secret or None
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_request",
        )

    return client_id, client_secret


def _validate_resource(request: Request, resource: str | None) -> None:
    if resource is None:
        return
    expected_resource = f"{_public_base_url(request)}/mcp"
    if resource != expected_resource:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_target",
        )


def _public_base_url(request: Request) -> str:
    return (settings.public_base_url or str(request.base_url)).rstrip("/")


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
