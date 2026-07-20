from tortoise import fields
from tortoise.models import Model


class ApiClient(Model):
    id = fields.IntField(primary_key=True)
    client_id = fields.CharField(max_length=80, unique=True, index=True)
    client_secret_hash = fields.CharField(max_length=255)
    name = fields.CharField(max_length=120)
    scopes = fields.JSONField(default=list)
    status = fields.CharField(max_length=20, default="active", index=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    last_used_at = fields.DatetimeField(null=True)

    class Meta:
        table = "api_clients"


class AccessToken(Model):
    id = fields.IntField(primary_key=True)
    token_hash = fields.CharField(max_length=64, unique=True, index=True)
    client: fields.ForeignKeyRelation[ApiClient] = fields.ForeignKeyField(
        "models.ApiClient",
        related_name="access_tokens",
        on_delete=fields.CASCADE,
    )
    scopes = fields.JSONField(default=list)
    expires_at = fields.DatetimeField(index=True)
    revoked_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "access_tokens"


class ApiClientRedirectUri(Model):
    id = fields.IntField(primary_key=True)
    client: fields.ForeignKeyRelation[ApiClient] = fields.ForeignKeyField(
        "models.ApiClient",
        related_name="redirect_uris",
        on_delete=fields.CASCADE,
    )
    redirect_uri = fields.CharField(max_length=500, index=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "api_client_redirect_uris"
        unique_together = (("client", "redirect_uri"),)


class AuthorizationCode(Model):
    id = fields.IntField(primary_key=True)
    code_hash = fields.CharField(max_length=64, unique=True, index=True)
    client: fields.ForeignKeyRelation[ApiClient] = fields.ForeignKeyField(
        "models.ApiClient",
        related_name="authorization_codes",
        on_delete=fields.CASCADE,
    )
    redirect_uri = fields.CharField(max_length=500)
    scopes = fields.JSONField(default=list)
    code_challenge = fields.CharField(max_length=128)
    code_challenge_method = fields.CharField(max_length=10)
    expires_at = fields.DatetimeField(index=True)
    consumed_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "authorization_codes"
