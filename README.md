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
- Publishes RFC 9728 MCP resource metadata and rotates opaque refresh tokens.
- Mounts FastAPI routes as MCP tools at `/mcp`.

## Quick Start

### 1. Install Requirements

This project uses `uv` and requires Python `3.14` or newer.

```bash
uv sync
```

### 2. Create the Database and Start the API

```bash
uv run python -m app.cli migrate
uv run fastapi dev
```

`migrate` creates or upgrades the SQLite database at `DATABASE_URL`; see
[Database Migrations](#database-migrations).

The API runs at:

```text
http://localhost:8000
```

Useful public endpoints:

- `GET /health`
- `GET /docs`
- `GET /.well-known/oauth-authorization-server`
- `GET /.well-known/oauth-protected-resource/mcp`
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
| `GET /weather?city=Nairobi&country_code=KE` | `weather:read` | Fetch current weather and save the lookup under the calling client |
| `GET /weather/history?limit=20` | `weather:history:read` | List the calling client's own lookups from the last `WEATHER_HISTORY_RETENTION_DAYS` (default 30) days |
| `GET /authorize` | Public | Start OAuth authorization-code flow with PKCE |
| `POST /register` | Public | Dynamically register an OAuth PKCE client |
| `POST /oauth/token` | Public | Exchange client credentials or authorization code for a bearer token |
| `GET /.well-known/oauth-authorization-server` | Public | OAuth metadata |
| `GET /.well-known/oauth-protected-resource/mcp` | Public | MCP resource metadata |
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
- Refresh-token rotation for authorization-code clients.

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
metadata. Registrations using `token_endpoint_auth_method: none` are public and
receive no client secret. Registrations using `client_secret_post` or
`client_secret_basic` are confidential and receive a one-time client secret.
All authorization-code clients use S256 PKCE. Loopback redirect URIs registered
without a port accept a dynamic port during authorization. If `scope` is
omitted or blank, the client receives only `weather:read`; access to weather
history must be requested explicitly.

OpenWebUI-compatible registration may request:

```json
{
  "client_name": "Open WebUI",
  "redirect_uris": ["http://localhost:3000/oauth/clients/example/callback"],
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "client_secret_post",
  "scope": "weather:read"
}
```

Authorization-code exchanges return both an access token and a refresh token.
Refresh tokens are opaque, stored only as hashes, single use, and rotated on
every successful `grant_type=refresh_token` request. Replaying an old refresh
token revokes its active token family.

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

Settings are read from environment variables or `.env`. Copy `.env.example` to `.env` to start; it lists every variable.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_NAME` | `smart-weather` | FastAPI application title |
| `DATABASE_URL` | `sqlite://smart_weather.sqlite3` | Database connection URL |
| `RUN_DB_MIGRATIONS_ON_STARTUP` | `false` | Apply pending migrations when the app starts (single-process deployments only) |
| `GENERATE_DB_SCHEMAS` | `false` | Create missing tables from the models on startup, bypassing migrations; for throwaway databases |
| `WEATHER_CLIENT_TIMEOUT` | `10.0` | Open-Meteo request timeout in seconds |
| `ACCESS_TOKEN_TTL_SECONDS` | `900` | Bearer token lifetime |
| `AUTHORIZATION_CODE_TTL_SECONDS` | `300` | Authorization code lifetime |
| `REFRESH_TOKEN_TTL_SECONDS` | `2592000` | Rotating refresh token lifetime |
| `PUBLIC_BASE_URL` | unset | Trusted external OAuth origin when deployed behind a proxy |
| `AI_MODEL` | `openai:gpt-4o-mini` | LangChain `provider:model` string for the `/ai/ask` agent |
| `OPENAI_API_KEY` | unset | API key for `openai:` models |
| `AI_API_KEY` | unset | API key for any provider; overrides `OPENAI_API_KEY` |
| `AI_TEMPERATURE` | `0.0` | Model sampling temperature |
| `AI_MAX_OUTPUT_TOKENS` | `1024` | Output token cap per model call |
| `AI_TIMEOUT` | `30.0` | Per model call timeout in seconds |
| `AI_MAX_RETRIES` | `1` | Per model call retries; SDK retries count against provider rate limits |
| `AI_REQUEST_TIMEOUT` | `60.0` | Timeout for the whole `/ai/ask` run in seconds |
| `AI_MAX_STEPS` | `8` | Agent recursion limit (each model call and tool round is one step) |
| `AI_MAX_CONCURRENCY` | `10` | Max concurrent `/ai/ask` runs per process; excess requests get `429` |
| `LANGSMITH_TRACING` | `false` | Send agent traces to LangSmith |
| `LANGSMITH_API_KEY` | unset | LangSmith API key; tracing stays off without it |
| `LANGSMITH_PROJECT` | `smart-weather` | LangSmith project that receives traces |
| `LANGSMITH_ENDPOINT` | unset | Set for the EU region (`https://eu.api.smith.langchain.com`) or self-hosted |
| `LANGSMITH_HIDE_INPUTS` | `false` | Redact questions in traces |
| `LANGSMITH_HIDE_OUTPUTS` | `false` | Redact answers in traces |

Example local `.env`:

```dotenv
DATABASE_URL=sqlite://smart_weather.sqlite3
ACCESS_TOKEN_TTL_SECONDS=900
REFRESH_TOKEN_TTL_SECONDS=2592000
PUBLIC_BASE_URL=https://weather.example.com
```

### LangSmith tracing

Traces show each model call, tool call, token count and latency for `/ai/ask`,
tagged with `client_id` and `ai_model`. To enable them, create an API key at
[smith.langchain.com](https://smith.langchain.com) and add to `.env`:

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your key>
```

Traces include the user's question and the model's answer. Set
`LANGSMITH_HIDE_INPUTS=true` and `LANGSMITH_HIDE_OUTPUTS=true` if that is
sensitive. Pending traces are flushed on shutdown.

## Database Migrations

The schema is managed by Tortoise ORM's built-in migrations in
`app/migrations/`. The app no longer creates tables at startup:
`GENERATE_DB_SCHEMAS` now defaults to `false`, because it only creates missing
tables and never alters existing ones.

Apply pending migrations (safe to re-run; it only moves forward):

```bash
uv run python -m app.cli migrate            # everything pending
uv run python -m app.cli migrate --dry-run  # show the plan only
```

Run this as a deploy step before starting the new version. The app logs a
warning at startup if migrations are pending. `RUN_DB_MIGRATIONS_ON_STARTUP=true`
makes the app apply them itself, which is convenient for a single local
process. Leave it off when several workers or replicas share a database,
because they would race to run the same DDL.

After changing a model, generate a migration, review it and its SQL, and commit it:

```bash
uv run python -m tortoise -c app.db.TORTOISE_ORM makemigrations -n short_description
uv run python -m tortoise -c app.db.TORTOISE_ORM sqlmigrate models 0003
```

Other Tortoise commands use the same config: `history` (applied), `heads`
(latest on disk) and `downgrade models <name>` (roll back to that migration).
`tests/test_migrations.py` fails if the models and migrations disagree.

### Adopting an Existing Database

Databases created before migrations existed were built by
`GENERATE_DB_SCHEMAS`. They have no migration history, so `migrate` would try
to create tables that already exist. Back up the database, then record the
schema the database already has as applied without running it (`--fake`),
and apply the rest:

```bash
# Database created from main before migrations (no access_tokens.family_id):
uv run python -m app.cli migrate 0001_initial --fake
uv run python -m app.cli migrate

# Database already created from these models (has family_id and weather_lookups.client_id):
uv run python -m app.cli migrate --fake
```

`uv run python -m tortoise -c app.db.TORTOISE_ORM history` shows what is
recorded. Only use `--fake` for migrations whose changes the database already
has.

## Development Notes

The project has no required weather API key because Open-Meteo is public.

The default database is a local SQLite file. To use a clean database for local
experiments, point `DATABASE_URL` at another SQLite path:

```bash
DATABASE_URL=sqlite:///tmp/smart_weather_dev.sqlite3 RUN_DB_MIGRATIONS_ON_STARTUP=true uv run fastapi dev
```

Run the tests (in-memory SQLite, no network):

```bash
uv run pytest
```

Evaluate the `/ai/ask` agent's answer quality (real model calls, which cost
money; weather data comes from fixtures). See [evals/README.md](evals/README.md):

```bash
uv run python -m evals.run --cases 'cw-*' --no-judge
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
