import re
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import HTTPException, status

from app.config import settings
from app.permissions import ALL_SCOPES, WEATHER_READ
from app.repositories.auth import (
    add_redirect_uri_to_client,
    consume_authorization_code,
    create_access_token,
    create_api_client,
    create_api_client_with_redirect_uris,
    create_authorization_code,
    create_refresh_token,
    get_access_token_by_hash,
    get_authorization_code_by_hash,
    get_api_client_by_client_id,
    get_refresh_token_by_hash,
    list_redirect_uris_for_client,
    list_api_clients,
    revoke_access_tokens_for_client,
    revoke_refresh_tokens_for_client,
    rotate_refresh_token,
    update_api_client_secret_hash,
    update_api_client_last_used,
    update_api_client_status,
)
from app.schemas.auth import (
    ApiClientCreated,
    AuthenticatedClient,
    DynamicClientRegistrationRequest,
    DynamicClientRegistrationResponse,
    TokenResponse,
)
from app.security import (
    generate_access_token,
    generate_authorization_code,
    generate_client_id,
    generate_client_secret,
    generate_refresh_token,
    generate_token_family_id,
    hash_secret,
    hash_token,
    utc_now,
    verify_pkce_s256,
    verify_secret,
)


TOKEN_TYPE = "Bearer"
SUPPORTED_DYNAMIC_GRANT_TYPE_SETS = {
    frozenset({"authorization_code"}),
    frozenset({"authorization_code", "refresh_token"}),
}
SUPPORTED_DYNAMIC_RESPONSE_TYPES = ["code"]
PUBLIC_TOKEN_AUTH_METHOD = "none"
CONFIDENTIAL_TOKEN_AUTH_METHODS = {
    "client_secret_basic",
    "client_secret_post",
}
UNUSABLE_CLIENT_SECRET_HASH = "!"
DYNAMIC_CLIENT_SECRET_HASH_PREFIX = "dcr:"
PRIVATE_USE_SCHEME_PATTERN = re.compile(
    r"^[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+$"
)
SUPPORTED_EDITOR_REDIRECT_SCHEMES = {
    "cursor",
    "vscode",
    "vscode-insiders",
}


class DynamicClientRegistrationError(ValueError):
    """RFC 7591 registration error safe to expose to clients."""

    def __init__(self, error: str, description: str):
        super().__init__(description)
        self.error = error
        self.description = description


def normalize_scopes(scope: str | None) -> list[str]:
    """Normalize an OAuth scope string into a stable, deduplicated list."""
    if not scope:
        return []
    return sorted(set(scope.split()))


async def register_api_client(name: str, scopes: list[str]) -> ApiClientCreated:
    """Create an API client and return its one-time plaintext secret."""
    client_id = generate_client_id()
    client_secret = generate_client_secret()
    client = await create_api_client(
        client_id=client_id,
        client_secret_hash=hash_secret(client_secret),
        name=name,
        scopes=sorted(set(scopes)),
    )
    return ApiClientCreated(
        client_id=client.client_id,
        client_secret=client_secret,
        name=client.name,
        scopes=list(client.scopes),
        status=client.status,
        created_at=client.created_at,
    )


async def register_dynamic_api_client(
    registration: DynamicClientRegistrationRequest,
) -> DynamicClientRegistrationResponse:
    """Validate and atomically register a public or confidential OAuth client."""
    redirect_uris = _normalize_and_validate_redirect_uris(
        registration.redirect_uris
    )
    grant_types = sorted(set(registration.grant_types))
    response_types = sorted(set(registration.response_types))

    if frozenset(grant_types) not in SUPPORTED_DYNAMIC_GRANT_TYPE_SETS:
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            "Supported grants are authorization_code with optional refresh_token.",
        )
    if response_types != SUPPORTED_DYNAMIC_RESPONSE_TYPES:
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            "Only the code response type is supported.",
        )
    if registration.token_endpoint_auth_method not in {
        PUBLIC_TOKEN_AUTH_METHOD,
        *CONFIDENTIAL_TOKEN_AUTH_METHODS,
    }:
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            "Unsupported token endpoint authentication method.",
        )

    try:
        requested_scope = (
            registration.scope.strip()
            if registration.scope and registration.scope.strip()
            else WEATHER_READ
        )
        scopes = _select_scopes(requested_scope, sorted(ALL_SCOPES))
    except HTTPException as exc:
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            "The requested scope contains an unsupported value.",
        ) from exc

    client_name = (registration.client_name or "").strip() or "Dynamic client"
    client_secret = (
        None
        if registration.token_endpoint_auth_method == PUBLIC_TOKEN_AUTH_METHOD
        else generate_client_secret()
    )
    client = await create_api_client_with_redirect_uris(
        client_id=generate_client_id(),
        client_secret_hash=(
            UNUSABLE_CLIENT_SECRET_HASH
            if client_secret is None
            else f"{DYNAMIC_CLIENT_SECRET_HASH_PREFIX}{hash_secret(client_secret)}"
        ),
        name=client_name,
        scopes=scopes,
        redirect_uris=redirect_uris,
    )
    return DynamicClientRegistrationResponse(
        client_id=client.client_id,
        client_id_issued_at=int(client.created_at.timestamp()),
        client_name=client.name,
        redirect_uris=redirect_uris,
        grant_types=grant_types,
        response_types=response_types,
        token_endpoint_auth_method=registration.token_endpoint_auth_method,
        scope=" ".join(scopes),
        client_secret=client_secret,
        client_secret_expires_at=0 if client_secret is not None else None,
    )


async def rotate_api_client_secret(client_id: str) -> ApiClientCreated:
    """Rotate a client secret and revoke existing access tokens for that client."""
    client = await get_api_client_by_client_id(client_id)
    if client is None:
        raise ValueError(f"No API client found for client_id {client_id}")

    client_secret = generate_client_secret()
    now = utc_now()
    secret_hash = hash_secret(client_secret)
    if client.client_secret_hash.startswith(DYNAMIC_CLIENT_SECRET_HASH_PREFIX):
        secret_hash = f"{DYNAMIC_CLIENT_SECRET_HASH_PREFIX}{secret_hash}"
    await update_api_client_secret_hash(client, secret_hash)
    await revoke_access_tokens_for_client(client, now)
    await revoke_refresh_tokens_for_client(client, now)

    return ApiClientCreated(
        client_id=client.client_id,
        client_secret=client_secret,
        name=client.name,
        scopes=list(client.scopes),
        status=client.status,
        created_at=client.created_at,
    )


async def disable_api_client(client_id: str) -> int:
    """Disable a client and revoke all active access tokens for immediate lockout."""
    client = await get_api_client_by_client_id(client_id)
    if client is None:
        raise ValueError(f"No API client found for client_id {client_id}")

    now = utc_now()
    await update_api_client_status(client, "disabled")
    access_count = await revoke_access_tokens_for_client(client, now)
    await revoke_refresh_tokens_for_client(client, now)
    return access_count


async def list_registered_api_clients() -> list[dict[str, object]]:
    """Return client inventory data without exposing secrets or token values."""
    clients = await list_api_clients()
    return [
        {
            "client_id": client.client_id,
            "name": client.name,
            "scopes": list(client.scopes),
            "redirect_uris": await list_redirect_uris_for_client(client),
            "status": client.status,
            "created_at": client.created_at,
            "last_used_at": client.last_used_at,
        }
        for client in clients
    ]


async def add_api_client_redirect_uri(client_id: str, redirect_uri: str) -> list[str]:
    """Register an exact redirect URI allowed for authorization-code redirects."""
    client = await get_api_client_by_client_id(client_id)
    if client is None:
        raise ValueError(f"No API client found for client_id {client_id}")

    await add_redirect_uri_to_client(client=client, redirect_uri=redirect_uri)
    return await list_redirect_uris_for_client(client)


async def create_authorization_code_redirect(
    *,
    client_id: str,
    response_type: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str | None,
) -> tuple[str, list[str]]:
    """Validate an authorization request and persist a one-time PKCE code."""
    if response_type != "code":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unsupported_response_type",
        )

    if code_challenge_method != "S256":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_code_challenge_method",
        )

    if not code_challenge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_code_challenge",
        )

    client = await get_api_client_by_client_id(client_id)
    if client is None or client.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_client",
        )

    registered_redirect_uris = await list_redirect_uris_for_client(client)
    if not _redirect_uri_is_allowed(redirect_uri, registered_redirect_uris):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_redirect_uri",
        )

    requested_scopes = _select_scopes(scope, client.scopes)
    code = generate_authorization_code()
    await create_authorization_code(
        code_hash=hash_token(code),
        client=client,
        redirect_uri=redirect_uri,
        scopes=requested_scopes,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        expires_at=utc_now()
        + timedelta(seconds=settings.authorization_code_ttl_seconds),
    )
    return code, requested_scopes


async def issue_client_credentials_token(
    *,
    grant_type: str,
    client_id: str,
    client_secret: str,
    scope: str | None,
) -> TokenResponse:
    """Issue a bearer token for a confidential client using its client secret."""
    if grant_type != "client_credentials":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unsupported_grant_type",
        )

    client = await get_api_client_by_client_id(client_id)
    if (
        client is None
        or client.client_secret_hash.startswith(DYNAMIC_CLIENT_SECRET_HASH_PREFIX)
        or not verify_secret(client_secret, client.client_secret_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_client",
            headers={"WWW-Authenticate": "Basic"},
        )

    if client.status != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_client",
            headers={"WWW-Authenticate": "Basic"},
        )

    return await _issue_access_token(
        client=client,
        scopes=_select_scopes(scope, client.scopes),
    )


async def issue_authorization_code_token(
    *,
    grant_type: str,
    client_id: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    client_secret: str | None,
) -> TokenResponse:
    """Exchange a valid authorization code and PKCE verifier for a bearer token."""
    if grant_type != "authorization_code":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unsupported_grant_type",
        )

    authorization_code = await get_authorization_code_by_hash(hash_token(code))
    if (
        authorization_code is None
        or authorization_code.consumed_at is not None
        or authorization_code.expires_at <= utc_now()
        or authorization_code.client.client_id != client_id
        or authorization_code.client.status != "active"
        or authorization_code.redirect_uri != redirect_uri
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_grant",
        )

    await _authenticate_token_client(
        client_id=client_id,
        client_secret=client_secret,
        client=authorization_code.client,
    )

    if authorization_code.code_challenge_method != "S256" or not verify_pkce_s256(
        code_verifier,
        authorization_code.code_challenge,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_grant",
        )

    await consume_authorization_code(authorization_code, utc_now())
    effective_scopes = sorted(
        set(authorization_code.scopes) & set(authorization_code.client.scopes)
    )
    return await _issue_access_and_refresh_token(
        client=authorization_code.client,
        scopes=effective_scopes,
    )


async def issue_refresh_token(
    *,
    client_id: str,
    client_secret: str | None,
    refresh_token: str,
    scope: str | None,
) -> TokenResponse:
    """Rotate a refresh token and issue a new access-token pair."""
    stored_token = await get_refresh_token_by_hash(hash_token(refresh_token))
    if stored_token is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_grant",
        )

    client = await _authenticate_token_client(
        client_id=client_id,
        client_secret=client_secret,
        client=stored_token.client,
    )
    allowed_scopes = sorted(
        set(stored_token.scopes) & set(client.scopes)
    )
    requested_scopes = _select_scopes(scope, allowed_scopes)
    access_token = generate_access_token()
    replacement_token = generate_refresh_token()
    now = utc_now()
    rotation_status, _ = await rotate_refresh_token(
        current_token_hash=hash_token(refresh_token),
        new_token_hash=hash_token(replacement_token),
        access_token_hash=hash_token(access_token),
        scopes=requested_scopes,
        access_expires_at=now
        + timedelta(seconds=settings.access_token_ttl_seconds),
        refresh_expires_at=now
        + timedelta(seconds=settings.refresh_token_ttl_seconds),
        rotated_at=now,
    )
    if rotation_status != "rotated":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_grant",
        )

    await update_api_client_last_used(client, now)
    return TokenResponse(
        access_token=access_token,
        expires_in=settings.access_token_ttl_seconds,
        scope=" ".join(requested_scopes),
        refresh_token=replacement_token,
    )


async def _authenticate_token_client(
    *,
    client_id: str,
    client_secret: str | None,
    client,
):
    """Authenticate public or confidential clients for token grants."""
    if client.client_id != client_id or client.status != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_client",
            headers={"WWW-Authenticate": "Basic"},
        )

    is_public = client.client_secret_hash == UNUSABLE_CLIENT_SECRET_HASH
    if is_public:
        if client_secret is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_client",
                headers={"WWW-Authenticate": "Basic"},
            )
        return client

    stored_secret_hash = client.client_secret_hash.removeprefix(
        DYNAMIC_CLIENT_SECRET_HASH_PREFIX
    )
    if client_secret is None or not verify_secret(client_secret, stored_secret_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_client",
            headers={"WWW-Authenticate": "Basic"},
        )
    return client


async def authenticate_bearer_token(token: str) -> AuthenticatedClient:
    """Authenticate bearer tokens and apply current client scopes dynamically."""
    access_token = await get_access_token_by_hash(hash_token(token))
    if (
        access_token is None
        or access_token.revoked_at is not None
        or access_token.expires_at <= utc_now()
        or access_token.client.status != "active"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_token",
            headers={"WWW-Authenticate": f'{TOKEN_TYPE} error="invalid_token"'},
        )

    effective_scopes = set(access_token.scopes) & set(access_token.client.scopes)
    return AuthenticatedClient(
        id=access_token.client.id,
        client_id=access_token.client.client_id,
        name=access_token.client.name,
        scopes=effective_scopes,
    )


def _select_scopes(scope: str | None, allowed_scopes: list[str]) -> list[str]:
    """Choose requested scopes, ensuring they are all assigned to the client."""
    requested_scopes = normalize_scopes(scope)
    allowed_scope_set = set(allowed_scopes)
    if not requested_scopes:
        return sorted(allowed_scope_set)

    if not set(requested_scopes).issubset(allowed_scope_set):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_scope",
        )

    return requested_scopes


def _redirect_uri_is_allowed(
    redirect_uri: str,
    registered_redirect_uris: list[str],
) -> bool:
    """Match exact redirects and native-app loopback redirects with dynamic ports."""
    if redirect_uri in registered_redirect_uris:
        return True

    return any(
        _loopback_redirect_uri_matches(registered_redirect_uri, redirect_uri)
        for registered_redirect_uri in registered_redirect_uris
    )


def _normalize_and_validate_redirect_uris(redirect_uris: list[str]) -> list[str]:
    """Return unique RFC 7591 redirect URIs or raise a protocol error."""
    normalized = list(dict.fromkeys(redirect_uris))
    if not normalized:
        raise DynamicClientRegistrationError(
            "invalid_redirect_uri",
            "At least one redirect URI is required.",
        )

    if any(not _is_valid_registration_redirect_uri(uri) for uri in normalized):
        raise DynamicClientRegistrationError(
            "invalid_redirect_uri",
            "Each redirect URI must be absolute, fragment-free, and use HTTPS, "
            "loopback HTTP, or a private-use scheme.",
        )
    return normalized


def _is_valid_registration_redirect_uri(redirect_uri: str) -> bool:
    if not redirect_uri or len(redirect_uri) > 500:
        return False
    try:
        parsed = urlsplit(redirect_uri)
        parsed.port
    except ValueError:
        return False

    if not parsed.scheme or parsed.fragment:
        return False
    if parsed.scheme == "https":
        return bool(parsed.hostname)
    if parsed.scheme == "http":
        return _is_loopback_host(parsed.hostname)
    return (
        parsed.scheme in SUPPORTED_EDITOR_REDIRECT_SCHEMES
        or PRIVATE_USE_SCHEME_PATTERN.fullmatch(parsed.scheme) is not None
    ) and bool(parsed.path or parsed.netloc)


def _loopback_redirect_uri_matches(
    registered_redirect_uri: str,
    requested_redirect_uri: str,
) -> bool:
    """Allow registered loopback redirects to vary only by port."""
    registered = urlsplit(registered_redirect_uri)
    requested = urlsplit(requested_redirect_uri)

    if registered.scheme != "http" or requested.scheme != "http":
        return False

    if not _is_loopback_host(registered.hostname):
        return False

    if _normalize_hostname(registered.hostname) != _normalize_hostname(requested.hostname):
        return False

    if (
        registered.path != requested.path
        or registered.query != requested.query
        or registered.fragment
        or requested.fragment
    ):
        return False

    return registered.port is None and requested.port is not None


def _is_loopback_host(hostname: str | None) -> bool:
    """Return true for loopback hosts supported by native OAuth clients."""
    return _normalize_hostname(hostname) in {"127.0.0.1", "localhost", "::1"}


def _normalize_hostname(hostname: str | None) -> str:
    """Normalize parsed redirect hostnames for loopback comparison."""
    return (hostname or "").lower()


async def _issue_access_token(*, client, scopes: list[str]) -> TokenResponse:
    """Persist a hashed opaque access token and return the plaintext token once."""
    token = generate_access_token()
    expires_in = settings.access_token_ttl_seconds
    await create_access_token(
        token_hash=hash_token(token),
        client=client,
        scopes=scopes,
        expires_at=utc_now() + timedelta(seconds=expires_in),
    )
    await update_api_client_last_used(client, utc_now())

    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        scope=" ".join(scopes),
    )


async def _issue_access_and_refresh_token(
    *,
    client,
    scopes: list[str],
) -> TokenResponse:
    """Issue an access token and initial opaque refresh token."""
    access_token = generate_access_token()
    refresh_token = generate_refresh_token()
    now = utc_now()
    await create_access_token(
        token_hash=hash_token(access_token),
        client=client,
        scopes=scopes,
        expires_at=now + timedelta(seconds=settings.access_token_ttl_seconds),
    )
    await create_refresh_token(
        token_hash=hash_token(refresh_token),
        family_id=generate_token_family_id(),
        client=client,
        scopes=scopes,
        expires_at=now + timedelta(seconds=settings.refresh_token_ttl_seconds),
    )
    await update_api_client_last_used(client, now)
    return TokenResponse(
        access_token=access_token,
        expires_in=settings.access_token_ttl_seconds,
        scope=" ".join(scopes),
        refresh_token=refresh_token,
    )
