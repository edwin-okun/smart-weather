# smart-weather

A project created with FastAPI CLI. Uses Layered approach since its small

The weather endpoint uses Open-Meteo, so no weather API key is required.
Weather lookups are saved to SQLite using Tortoise ORM.
Weather API routes require OAuth client-credentials authentication.

## Quick Start

### Start the development server

```bash
uv run fastapi dev
```

Visit http://localhost:8000

Create an API client:

```bash
uv run python -m app.cli create-client --name local-dev
```

The command prints a one-time `client_secret`. Store it somewhere safe because
only its hash is saved.

Request an access token:

```bash
curl -X POST "http://localhost:8000/oauth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=$CLIENT_ID" \
  -d "client_secret=$CLIENT_SECRET" \
  -d "scope=weather:read weather:history:read"
```

Example request:

```bash
curl "http://localhost:8000/weather?city=Nairobi" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

View recent saved lookups:

```bash
curl "http://localhost:8000/weather/history" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

### Authentication

This app supports two OAuth flows:

- Client credentials for machine-to-machine API access.
- Authorization code with PKCE for AI clients and public clients that launch a browser authorization flow.

Shared behavior:

- `POST /oauth/token` issues short-lived opaque bearer tokens.
- `GET /authorize` issues short-lived authorization codes for registered redirect URIs.
- `GET /.well-known/oauth-authorization-server` exposes OAuth metadata for dynamic clients.
- `GET /weather` requires the `weather:read` scope.
- `GET /weather/history` requires the `weather:history:read` scope.
- `GET /health` remains public for service health checks.
- Client secrets, access tokens, and authorization codes are stored only as hashes.

Create clients with explicit scopes:

```bash
uv run python -m app.cli create-client \
  --name partner-service \
  --scope weather:read \
  --scope weather:history:read
```

For VS Code or AI clients that redirect through `https://vscode.dev/redirect`,
register that exact redirect URI:

```bash
uv run python -m app.cli add-redirect-uri \
  --client-id "$CLIENT_ID" \
  --redirect-uri "https://vscode.dev/redirect"
```

For native clients that use a local callback with a random port, register the
loopback URI without a port. Requests such as `http://127.0.0.1:33418/` will
match this registered URI:

```bash
uv run python -m app.cli add-redirect-uri \
  --client-id "$CLIENT_ID" \
  --redirect-uri "http://127.0.0.1/"
```

The authorization-code flow then uses:

```text
GET /authorize?client_id=$CLIENT_ID&response_type=code&redirect_uri=https%3A%2F%2Fvscode.dev%2Fredirect&code_challenge=...&code_challenge_method=S256
```

The client exchanges the returned `code` with:

```bash
curl -X POST "http://localhost:8000/oauth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=authorization_code" \
  -d "client_id=$CLIENT_ID" \
  -d "code=$CODE" \
  -d "redirect_uri=https://vscode.dev/redirect" \
  -d "code_verifier=$CODE_VERIFIER"
```

Rotate a client secret and revoke its active tokens:

```bash
uv run python -m app.cli rotate-secret --client-id "$CLIENT_ID"
```

Disable a client and revoke its active tokens:

```bash
uv run python -m app.cli disable-client --client-id "$CLIENT_ID"
```

List clients without revealing secrets:

```bash
uv run python -m app.cli list-clients
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

The MCP endpoint requires the same bearer token:

```text
Authorization: Bearer $ACCESS_TOKEN
```

Available MCP tools are generated from the OpenAPI operation IDs:

- `get_weather` - fetches current weather for a city and saves the successful lookup to SQLite
- `list_weather_history` - returns recent saved weather lookups from SQLite
- `health` - returns service health status

The token endpoint is intentionally not exposed as an MCP tool because OAuth
token requests use form encoding.

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
- `ACCESS_TOKEN_TTL_SECONDS` - bearer token lifetime, defaults to `900`
- `AUTHORIZATION_CODE_TTL_SECONDS` - authorization code lifetime, defaults to `300`

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
- `app/security.py` - Token and secret generation/hashing helpers
- `app/dependencies.py` - FastAPI auth dependencies and scope enforcement
- `app/permissions.py` - Permission scope constants
- `app/cli.py` - API client provisioning and lifecycle commands
- `app/db.py` - Tortoise ORM setup and shutdown
- `app/config.py` - Environment-backed application settings
- `pyproject.toml` - Project dependencies

## Learn More

- [FastAPI Documentation](https://fastapi.tiangolo.com)
- [FastAPI Cloud](https://fastapicloud.com)
