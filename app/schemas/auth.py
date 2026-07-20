from datetime import datetime

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str = ""


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
