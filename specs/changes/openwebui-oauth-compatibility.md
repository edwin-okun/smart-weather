# OpenWebUI OAuth 2.1 Compatibility

## Problem

OpenWebUI reaches the authorization-server metadata and Dynamic Client
Registration endpoint, but registration fails because it requests:

- `grant_types`: `["authorization_code", "refresh_token"]`; and
- `token_endpoint_auth_method`: `client_secret_post`.

The authorization server currently supports only public dynamically registered
clients using `authorization_code` and `none`. The MCP endpoint also lacks
RFC 9728 Protected Resource Metadata, so OpenWebUI performs fallback discovery
and requests the authorization server's full scope catalog instead of a
least-privilege MCP resource scope.

## Intended outcome

OpenWebUI can discover the protected MCP resource, dynamically register a
confidential client, complete S256 PKCE authorization, exchange and refresh
tokens, and call `/mcp`. Public PKCE clients and existing CLI-created
client-credentials clients remain compatible.

## Behavior

### Protected Resource Metadata

The service exposes identical RFC 9728 metadata at:

- `GET /.well-known/oauth-protected-resource`
- `GET /.well-known/oauth-protected-resource/mcp`

The response contains:

- `resource`: the absolute `/mcp` URL for the request origin;
- `authorization_servers`: an array containing the origin authorization
  server URL;
- `scopes_supported`: `["weather:read"]`; and
- `bearer_methods_supported`: `["header"]`.

An unauthenticated `/mcp` request returns `401 Unauthorized` with a
`WWW-Authenticate: Bearer` challenge containing an absolute
`resource_metadata` URL. The existing bearer-token error semantics remain
available for invalid or expired tokens.

Authorization-server discovery continues at
`/.well-known/oauth-authorization-server` and advertises
`authorization_code`, `refresh_token`, and `client_credentials` grant types.

### Dynamic Client Registration

`POST /register` accepts these grant-type sets:

- `["authorization_code"]`; or
- `["authorization_code", "refresh_token"]`.

It accepts these token endpoint authentication methods:

- `none`, creating a public client with no returned secret;
- `client_secret_post`, creating a confidential client; or
- `client_secret_basic`, creating a confidential client.

Confidential registrations return a generated `client_secret` exactly once
and `client_secret_expires_at: 0`. Only the secret hash is persisted. The
registration response echoes the effective grant types and authentication
method.

Unknown metadata such as OpenID Connect `application_type` remains ignored.
Existing redirect URI, body-size, scope, and open-registration safeguards
remain in force.

### Authorization and resource indicator

`GET /authorize` accepts an optional RFC 8707 `resource` parameter. When
present, it must exactly equal the absolute local `/mcp` resource URL;
otherwise the request fails with `invalid_target`. The authorization server
serves only this resource, so access and refresh tokens are implicitly
resource-bound without adding an audience column to existing token records.

S256 PKCE remains required for public and confidential authorization-code
clients. A confidential client must authenticate at the token endpoint using
its generated secret when exchanging a code.

### Token endpoint and refresh rotation

`POST /oauth/token` supports `grant_type=refresh_token` in addition to the
existing grants.

Successful authorization-code exchanges issue:

- the existing opaque access token;
- an opaque refresh token;
- `refresh_token` in the JSON token response; and
- the effective scope.

Because existing client rows do not store registered grant types, the server
may issue a refresh token for every successful authorization-code exchange.
This preserves compatibility without requiring a destructive migration;
clients that did not request refresh simply ignore the additional field.

Refresh tokens:

- are stored only as hashes;
- have a configurable lifetime, defaulting to 30 days;
- are bound to the issuing client and scope set;
- are single use and rotate on every successful refresh;
- belong to a token family;
- revoke the active family when a consumed token is replayed; and
- cannot expand scope beyond the original grant or the client's current
  allowed scopes.

Confidential clients authenticate refresh requests with their client secret.
Public clients provide their `client_id` and no secret. Invalid, expired,
revoked, replayed, wrong-client, or wrongly authenticated refresh requests
return `invalid_grant` or `invalid_client` without exposing internal details.

The token endpoint accepts the optional RFC 8707 `resource` parameter and
rejects a non-local resource with `invalid_target`.

### Client authentication compatibility

Existing CLI-created confidential clients continue to authenticate
client-credentials requests using Basic or form credentials. Existing public
dynamically registered clients continue to exchange authorization codes
without a secret.

For authorization-code and refresh-token grants, a client with a usable
persisted secret must authenticate with that secret; a client with the
unusable public-client hash must not be required or allowed to authenticate
with a guessed secret.

## Security and operational policy

- Refresh and access tokens are opaque, random, and persisted only as hashes.
- Refresh rotation and family revocation are atomic.
- Secret comparison uses the existing constant-time password-hash verifier.
- The MCP protected-resource metadata advertises only `weather:read`, avoiding
  anonymous DCR receiving global weather-history access by default.
- Open registration remains an unauthenticated write surface subject to the
  existing request limits and deployment-level rate limiting guidance.
- Token and refresh-token values must not appear in inventory commands or
  logs.

## Non-goals

- OpenID Connect ID tokens or a user-info endpoint.
- Consent, login, or multi-user resource-owner UI.
- Refresh tokens for the client-credentials grant.
- Multiple resource servers, arbitrary audiences, or external authorization
  servers.
- JWT access or refresh tokens, introspection, or revocation endpoints.
- RFC 7592 client registration management.
- Client ID Metadata Documents.
- Changing the global weather-history data model or adding per-client history.

## Acceptance criteria

- Both protected-resource metadata paths return the correct absolute MCP
  resource, authorization server, `weather:read` scope, and bearer method.
- Anonymous `/mcp` responses advertise the protected-resource metadata URL in
  `WWW-Authenticate`.
- Authorization-server metadata advertises refresh-token support.
- An OpenWebUI-shaped registration using `authorization_code`,
  `refresh_token`, and `client_secret_post` returns `201`, one plaintext
  secret, and matching effective metadata.
- A confidential registered client completes S256 authorization and an
  authenticated code exchange; an absent or wrong secret returns
  `invalid_client`.
- Public DCR clients still complete S256 authorization and code exchange
  without a secret.
- Authorization and token requests accept the local MCP resource and reject
  other resource values with `invalid_target`.
- Authorization-code exchange issues an access token and refresh token.
- A valid refresh rotates both tokens, preserves or narrows scope, and cannot
  expand scope.
- Refresh-token replay revokes the active token family and does not issue
  another token.
- Expired, revoked, wrong-client, and invalid refresh tokens fail without
  leaking token values or internal errors.
- Existing CLI confidential client-credentials behavior remains compatible.
- OAuth protocol endpoints remain excluded from generated MCP tools.
- OpenWebUI can register, authorize, refresh, and call `/mcp` in an isolated
  end-to-end smoke test.

## Migration and rollback

The implementation adds a refresh-token table with defaults suitable for
automatic schema creation. Existing client, access-token, authorization-code,
redirect, and weather tables remain unchanged so existing SQLite databases
retain their data.

Rollback removes refresh issuance, refresh grant handling, and protected
resource metadata routes. The added refresh-token table may remain unused;
rollback does not require destructive migration.

## Configuration

Add:

- `REFRESH_TOKEN_TTL_SECONDS`, default `2592000` (30 days).

## Verification

Automated verification covers discovery documents and challenges; OpenWebUI
DCR metadata; public and confidential PKCE; resource validation; refresh
issuance, authentication, scope rules, rotation, replay-family revocation,
expiry, and rollback; existing client credentials; database compatibility; and
MCP exclusion.

Run the repository's full unit suite, compilation check, lockfile check, and
any configured formatting, lint, and type checks.

The smoke-test gate will use an isolated disposable SQLite database and the
user-approved `tester` identity. It will exercise the OpenWebUI registration
shape, confidential S256 PKCE, token refresh, protected MCP initialization,
and a weather operation without touching existing data.
