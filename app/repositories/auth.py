from datetime import datetime

from app.models.auth import (
    AccessToken,
    ApiClient,
    ApiClientRedirectUri,
    AuthorizationCode,
)


async def create_api_client(
    *,
    client_id: str,
    client_secret_hash: str,
    name: str,
    scopes: list[str],
) -> ApiClient:
    return await ApiClient.create(
        client_id=client_id,
        client_secret_hash=client_secret_hash,
        name=name,
        scopes=scopes,
    )


async def get_api_client_by_client_id(client_id: str) -> ApiClient | None:
    return await ApiClient.get_or_none(client_id=client_id)


async def list_api_clients() -> list[ApiClient]:
    return await ApiClient.all().order_by("name", "client_id")


async def update_api_client_last_used(client: ApiClient, used_at: datetime) -> None:
    client.last_used_at = used_at
    await client.save(update_fields=["last_used_at", "updated_at"])


async def update_api_client_secret_hash(client: ApiClient, secret_hash: str) -> None:
    client.client_secret_hash = secret_hash
    await client.save(update_fields=["client_secret_hash", "updated_at"])


async def update_api_client_status(client: ApiClient, status: str) -> None:
    client.status = status
    await client.save(update_fields=["status", "updated_at"])


async def create_access_token(
    *,
    token_hash: str,
    client: ApiClient,
    scopes: list[str],
    expires_at: datetime,
) -> AccessToken:
    return await AccessToken.create(
        token_hash=token_hash,
        client=client,
        scopes=scopes,
        expires_at=expires_at,
    )


async def get_access_token_by_hash(token_hash: str) -> AccessToken | None:
    return await AccessToken.get_or_none(token_hash=token_hash).prefetch_related("client")


async def revoke_access_tokens_for_client(client: ApiClient, revoked_at: datetime) -> int:
    return await AccessToken.filter(client=client, revoked_at__isnull=True).update(
        revoked_at=revoked_at
    )


async def add_redirect_uri_to_client(
    *,
    client: ApiClient,
    redirect_uri: str,
) -> ApiClientRedirectUri:
    redirect, _ = await ApiClientRedirectUri.get_or_create(
        client=client,
        redirect_uri=redirect_uri,
    )
    return redirect


async def list_redirect_uris_for_client(client: ApiClient) -> list[str]:
    redirects = await ApiClientRedirectUri.filter(client=client).order_by("redirect_uri")
    return [redirect.redirect_uri for redirect in redirects]


async def client_has_redirect_uri(client: ApiClient, redirect_uri: str) -> bool:
    return await ApiClientRedirectUri.filter(
        client=client,
        redirect_uri=redirect_uri,
    ).exists()


async def create_authorization_code(
    *,
    code_hash: str,
    client: ApiClient,
    redirect_uri: str,
    scopes: list[str],
    code_challenge: str,
    code_challenge_method: str,
    expires_at: datetime,
) -> AuthorizationCode:
    return await AuthorizationCode.create(
        code_hash=code_hash,
        client=client,
        redirect_uri=redirect_uri,
        scopes=scopes,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        expires_at=expires_at,
    )


async def get_authorization_code_by_hash(
    code_hash: str,
) -> AuthorizationCode | None:
    return await AuthorizationCode.get_or_none(code_hash=code_hash).prefetch_related(
        "client"
    )


async def consume_authorization_code(
    authorization_code: AuthorizationCode,
    consumed_at: datetime,
) -> None:
    authorization_code.consumed_at = consumed_at
    await authorization_code.save(update_fields=["consumed_at"])
