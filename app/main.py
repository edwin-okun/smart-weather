import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.clients import weather_client
from app.config import settings
from app.db import close_db, init_db
from app.routers import health_router, weather_router

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
    app.include_router(health_router)
    app.include_router(weather_router)
    return app


app = create_app()
