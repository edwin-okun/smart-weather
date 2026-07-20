import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi_mcp import FastApiMCP
from fastapi_mcp.types import AuthConfig

from app.clients import weather_client
from app.config import settings
from app.db import close_db, init_db
from app.dependencies import get_current_client
from app.routers import auth_router, health_router, weather_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        yield
    finally:
        await weather_client.aclose()
        await close_db()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(auth_router)
    app.include_router(health_router)
    app.include_router(weather_router)
    return app


# Create the FastAPI app instance
app = create_app()

# Create an MCP server based on this app.
# The OAuth token endpoint uses form encoding, so keep token issuance on HTTP.
mcp = FastApiMCP(
    app,
    exclude_operations=["authorize_oauth_client", "issue_oauth_token"],
    auth_config=AuthConfig(dependencies=[Depends(get_current_client)]),
)


# Mount the MCP server directly to your app
mcp.mount_http()
