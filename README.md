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

### MCP Usage

This app exposes its FastAPI routes as MCP tools through `fastapi-mcp`.

Start the server:

```bash
uv run fastapi dev
```

Connect an MCP client to:

```text
http://localhost:8000/mcp
```

Available MCP tools are generated from the OpenAPI operation IDs:

- `get_weather` - fetches current weather for a city and saves the successful lookup to SQLite
- `list_weather_history` - returns recent saved weather lookups from SQLite
- `health` - returns service health status

`get_weather` parameters:

- `city` - city name, for example `Nairobi`
- `country_code` - optional ISO 3166-1 alpha-2 country code, defaults to `KE`

`list_weather_history` parameters:

- `limit` - optional number of saved lookups to return, from `1` to `100`, defaults to `20`

The MCP server is mounted in `app/main.py` with:

```python
mcp = FastApiMCP(app)
mcp.mount_http()
```

The default HTTP MCP mount path is `/mcp`.

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
