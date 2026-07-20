# smart-weather

A project created with FastAPI CLI. Uses Layered approach since its small

The weather endpoint uses Open-Meteo, so no weather API key is required.
Weather lookups are saved to SQLite using Tortoise ORM.

## Quick Start

### Start the development server

```bash
uv run fastapi dev
```

Visit http://localhost:8000

Example request:

```bash
curl "http://localhost:8000/weather?city=Nairobi"
```

View recent saved lookups:

```bash
curl "http://localhost:8000/weather/history"
```

### Configuration

Settings are read from environment variables or `.env`.

- `APP_NAME` - FastAPI application title
- `DATABASE_URL` - database connection URL, defaults to `sqlite://smart_weather.sqlite3`
- `GENERATE_DB_SCHEMAS` - auto-create database tables on startup, defaults to `true`
- `WEATHER_CLIENT_TIMEOUT` - external weather API timeout in seconds

### Deploy to FastAPI Cloud

Sign up and log in at https://fastapicloud.com, then deploy with:

```bash
uv run fastapi deploy
```

## Project Structure

- `app/main.py` - FastAPI application setup and router wiring
- `app/routers/` - HTTP route handlers
- `app/services/` - Application/business logic
- `app/repositories/` - Database access functions
- `app/models/` - Tortoise ORM models
- `app/schemas/` - Pydantic request and response models
- `app/clients.py` - Shared external HTTP clients
- `app/db.py` - Tortoise ORM setup and shutdown
- `app/config.py` - Environment-backed application settings
- `pyproject.toml` - Project dependencies

## Learn More

- [FastAPI Documentation](https://fastapi.tiangolo.com)
- [FastAPI Cloud](https://fastapicloud.com)
