# smart-weather

`smart-weather` is a small FastAPI service that returns current weather for a
city, stores successful lookups in SQLite, and exposes the same core operations
as MCP tools.

It is intentionally compact, but it includes production-shaped concerns:
OAuth-style API access, hashed secrets and tokens, scoped permissions, async
external API calls, persistence, and a layered code structure that is easy to
review in an interview.

## What It Does

- Finds a city through the Open-Meteo geocoding API.
- Fetches current weather from Open-Meteo without requiring a weather API key.
- Saves successful lookups to SQLite with Tortoise ORM.
- Protects weather routes with short-lived bearer tokens.
- Supports OAuth client credentials, authorization code with PKCE, and
  RFC 7591 Dynamic Client Registration.
- Mounts FastAPI routes as MCP tools at `/mcp`.

## Quick Start

### 1. Install Requirements

This project uses `uv` and requires Python `3.14` or newer.

```bash
uv sync
```

### 2. Start the API

```bash
uv run fastapi dev
```

The API runs at:

```text
http://localhost:8000
```

Useful public endpoints:

- `GET /health`
- `GET /docs`
- `GET /.well-known/oauth-authorization-server`
- `POST /register`

### 3. Create an API Client

In a second terminal, create a local client:

```bash
uv run python -m app.cli create-client --name local-dev
```

The command prints a `client_id` and one-time `client_secret`.
Save both values locally:

```bash
export CLIENT_ID="paste-client-id-here"
export CLIENT_SECRET="paste-client-secret-here"
```

Secrets are stored only as hashes, so the plaintext secret cannot be recovered
later. Rotate it if it is lost.

### 4. Request an Access Token

```bash
curl -X POST "http://localhost:8000/oauth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=$CLIENT_ID" \
  -d "client_secret=$CLIENT_SECRET" \
  -d "scope=weather:read weather:history:read"
```

Copy the returned `access_token`:

```bash
export ACCESS_TOKEN="paste-access-token-here"
```

### 5. Call the Weather API

```bash
curl "http://localhost:8000/weather?city=Nairobi" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

View saved lookups:

```bash
curl "http://localhost:8000/weather/history?limit=10" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

## API Overview

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /health` | Public | Service health check |
| `GET /weather?city=Nairobi&country_code=KE` | `weather:read` | Fetch current weather and save the lookup |
| `GET /weather/history?limit=20` | `weather:history:read` | List recent saved lookups |
| `GET /authorize` | Public | Start OAuth authorization-code flow with PKCE |
| `POST /register` | Public | Dynamically register a public PKCE client |
| `POST /oauth/token` | Public | Exchange client credentials or authorization code for a bearer token |
| `GET /.well-known/oauth-authorization-server` | Public | OAuth metadata |
| `/mcp` | Bearer token | MCP endpoint generated from FastAPI routes |

## Architecture

The app keeps framework, business, and persistence concerns separated:

- `app/main.py` wires FastAPI, routers, database lifecycle, and MCP.
- `app/routers/` contains HTTP route handlers.
- `app/services/` contains weather and auth business logic.
- `app/repositories/` contains Tortoise ORM database access.
- `app/models/` contains database models.
- `app/schemas/` contains Pydantic request and response models.
- `app/clients.py` contains the Open-Meteo HTTP client.
- `app/dependencies.py` contains auth dependencies and scope enforcement.
- `app/security.py` contains token generation, hashing, and PKCE helpers.
- `app/cli.py` contains local administration commands.

Request flow for `GET /weather`:

```text
router -> auth dependency -> weather service -> Open-Meteo client
       -> weather repository -> SQLite -> response schema
```

## Authentication

Weather routes require bearer tokens issued by `POST /oauth/token`.

Supported OAuth flows:

- Client credentials for machine-to-machine access.
- Authorization code with PKCE for public clients that launch a browser flow.

Security behavior:

- Client secrets are generated once and stored only as hashes.
- Access tokens and authorization codes are opaque and stored only as hashes.
- Access tokens are short lived. The default TTL is `900` seconds.
- Authorization codes are short lived. The default TTL is `300` seconds.
- Disabled clients and rotated secrets revoke active tokens for that client.

Available scopes:

- `weather:read`
- `weather:history:read`

### Dynamic Client Registration

OAuth and MCP clients can discover the registration endpoint through
`GET /.well-known/oauth-authorization-server`. Register a public
authorization-code client with JSON metadata:

```bash
curl -X POST "http://localhost:8000/register" \
  -H "Content-Type: application/json" \
  -d '{
    "client_name": "local-mcp-client",
    "redirect_uris": ["http://127.0.0.1/callback"],
    "grant_types": ["authorization_code"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "none",
    "scope": "weather:read weather:history:read"
  }'
```

The response contains a generated `client_id` and the effective registration
metadata. Dynamically registered clients are public clients: they receive no
client secret and use the authorization-code flow with S256 PKCE. Loopback
redirect URIs registered without a port accept a dynamic port during
authorization. If `scope` is omitted or blank, the client receives only
`weather:read`; access to weather history must be requested explicitly.

Registration is intentionally unauthenticated. For an internet-facing
deployment, protect `/register` with deployment-level rate limiting and
monitoring to limit automated abuse and unbounded client creation.

### Client Commands

Create a client with default scopes:

```bash
uv run python -m app.cli create-client --name partner-service
```

Create a client with explicit scopes:

```bash
uv run python -m app.cli create-client \
  --name partner-service \
  --scope weather:read \
  --scope weather:history:read
```

List clients without exposing secrets:

```bash
uv run python -m app.cli list-clients
```

Rotate a client secret and revoke active tokens:

```bash
uv run python -m app.cli rotate-secret --client-id "$CLIENT_ID"
```

Disable a client and revoke active tokens:

```bash
uv run python -m app.cli disable-client --client-id "$CLIENT_ID"
```

### PKCE Redirect URIs

For VS Code or AI clients that redirect through `https://vscode.dev/redirect`,
register that exact redirect URI:

```bash
uv run python -m app.cli add-redirect-uri \
  --client-id "$CLIENT_ID" \
  --redirect-uri "https://vscode.dev/redirect"
```

For native apps that use a local callback with a random port, register the
loopback URI without a port:

```bash
uv run python -m app.cli add-redirect-uri \
  --client-id "$CLIENT_ID" \
  --redirect-uri "http://127.0.0.1/"
```

Requests such as `http://127.0.0.1:33418/` will match that registered loopback
URI.

## MCP Usage

Start the FastAPI server:

```bash
uv run fastapi dev
```

Connect an MCP client to:

```text
http://localhost:8000/mcp
```

Use the same bearer token as the HTTP API:

```text
Authorization: Bearer $ACCESS_TOKEN
```

Available MCP tools are generated from OpenAPI operation IDs:

- `get_weather`
- `list_weather_history`
- `health`

The authorization, token, and dynamic registration operations are
intentionally excluded from generated MCP tools because they are OAuth
protocol endpoints rather than weather tools.

## Configuration

Settings are read from environment variables or `.env`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_NAME` | `smart-weather` | FastAPI application title |
| `DATABASE_URL` | `sqlite://smart_weather.sqlite3` | Database connection URL |
| `GENERATE_DB_SCHEMAS` | `true` | Auto-create database tables on startup |
| `WEATHER_CLIENT_TIMEOUT` | `10.0` | Open-Meteo request timeout in seconds |
| `ACCESS_TOKEN_TTL_SECONDS` | `900` | Bearer token lifetime |
| `AUTHORIZATION_CODE_TTL_SECONDS` | `300` | Authorization code lifetime |

Example local `.env`:

```dotenv
DATABASE_URL=sqlite://smart_weather.sqlite3
GENERATE_DB_SCHEMAS=true
ACCESS_TOKEN_TTL_SECONDS=900
```

## Development Notes

The project has no required weather API key because Open-Meteo is public.

The default database is a local SQLite file. To use a clean database for local
experiments, point `DATABASE_URL` at another SQLite path:

```bash
DATABASE_URL=sqlite:///tmp/smart_weather_dev.sqlite3 uv run fastapi dev
```

Run the CLI help:

```bash
uv run python -m app.cli --help
```

Deploy to FastAPI Cloud:

```bash
uv run fastapi deploy
```

## Interviewer Notes

This project is meant to be easy to inspect quickly:

- The core weather path is small and async end to end.
- External API access is isolated in `app/clients.py`.
- Persistence is behind repository functions.
- Auth logic is explicit, scoped, and testable without being hidden in a third-party provider.
- MCP support is mounted from the same FastAPI app instead of being a separate service.
