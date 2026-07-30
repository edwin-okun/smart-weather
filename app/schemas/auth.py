from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str = ""
    refresh_token: str | None = None


class AuthenticatedClient(BaseModel):
    id: int
    client_id: str
    name: str
    scopes: set[str] = Field(default_factory=set)


class ApiClientCreated(BaseModel):
    client_id: str
    client_secret: str
    name: str
    scopes: list[str]
    status: str
    created_at: datetime | None = None


class DynamicClientRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    redirect_uris: list[str] = Field(min_length=1, max_length=10)
    client_name: str | None = Field(default=None, max_length=120)
    grant_types: list[str] = Field(default_factory=lambda: ["authorization_code"])
    response_types: list[str] = Field(default_factory=lambda: ["code"])
    token_endpoint_auth_method: str = "none"
    scope: str | None = None


class DynamicClientRegistrationResponse(BaseModel):
    client_id: str
    client_id_issued_at: int
    client_name: str
    redirect_uris: list[str]
    grant_types: list[str]
    response_types: list[str]
    token_endpoint_auth_method: str
    scope: str
    client_secret: str | None = None
    client_secret_expires_at: int | None = None


class DynamicClientRegistrationErrorResponse(BaseModel):
    error: str
    error_description: str
