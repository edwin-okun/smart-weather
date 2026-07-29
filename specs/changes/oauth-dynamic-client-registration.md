# OAuth Dynamic Client Registration

## Problem

OAuth clients currently require an administrator to create a client with the
local CLI before they can use the authorization-code flow. This prevents MCP
clients with no prior relationship to the service from discovering the OAuth
authorization server and registering themselves.

## Intended outcome

The authorization server supports open OAuth 2.0 Dynamic Client Registration
as defined by RFC 7591 for public authorization-code clients using PKCE. A
client can discover the registration endpoint, register its redirect URIs and
allowed scopes, receive a client identifier, and immediately use that
identifier with the existing authorization-code flow.

## Behavior

### Discovery

`GET /.well-known/oauth-authorization-server` includes an absolute
`registration_endpoint` URL ending in `/register`.

### Registration request

`POST /register` accepts an unauthenticated `application/json` request with
RFC 7591 client metadata. The supported metadata is:

- `redirect_uris`: required, non-empty array of unique redirect URI strings.
- `client_name`: optional human-readable name.
- `grant_types`: optional; defaults to `["authorization_code"]` and, when
  supplied, must equal that supported set.
- `response_types`: optional; defaults to `["code"]` and, when supplied, must
  equal that supported set.
- `token_endpoint_auth_method`: optional; defaults to `none` and must be
  `none`.
- `scope`: optional space-delimited scopes. It defaults to all server-supported
  scopes and may contain only server-supported scopes.

Unknown client metadata is ignored as required by RFC 7591. Duplicate values
within supported array metadata are normalized in the response.

Each redirect URI must be absolute, contain no fragment, and satisfy one of:

- an `https` URI with a host;
- an `http` loopback URI using `127.0.0.1`, `[::1]`, or `localhost`; or
- an absolute private-use, non-HTTP URI scheme.

The existing authorization endpoint continues to require an exact registered
redirect URI, except for its existing native loopback dynamic-port matching.

### Registration response

On success, the endpoint persists the client and all redirect URIs atomically
and returns HTTP `201 Created` with `application/json`. The response contains:

- the issued `client_id`;
- `client_id_issued_at` as a Unix timestamp;
- the effective `client_name`, `redirect_uris`, `grant_types`,
  `response_types`, `token_endpoint_auth_method`, and `scope`.

Public dynamically registered clients receive no `client_secret`.

### Errors

Invalid requests return HTTP `400 Bad Request` with an RFC 7591 JSON error
object containing `error` and, when useful, `error_description`:

- `invalid_redirect_uri` for missing or unacceptable redirect metadata;
- `invalid_client_metadata` for unsupported grant types, response types,
  token authentication methods, scopes, or malformed supported metadata.

The endpoint does not expose persistence errors, secrets, or internal
exception details in responses.

## Security and operational policy

- Registration is deliberately open and does not accept an initial access
  token.
- Dynamically registered clients are public clients and must use the existing
  authorization-code flow with S256 PKCE.
- Registration grants only the existing server-supported scopes.
- Client secrets continue to be generated only by the administrative CLI.
- The README documents that open registration is an unauthenticated write
  surface and should be protected by deployment-level rate limiting in
  internet-facing environments.

## Non-goals

- OAuth Dynamic Client Registration Management (RFC 7592).
- Software statements, initial access tokens, registration access tokens, or
  client configuration endpoints.
- Dynamically registering confidential clients or enabling client credentials
  through `/register`.
- Client ID Metadata Documents.
- Consent UI, refresh tokens, OpenID Connect registration extensions, or
  automated expiry and cleanup of dynamically registered clients.
- Application-level distributed rate limiting.

## Acceptance criteria

- OAuth authorization-server metadata advertises the absolute registration
  endpoint.
- A conforming public client registration returns `201`, persists one client
  and its redirect URIs, returns no secret, and can start the existing PKCE
  authorization flow.
- Omitted optional protocol metadata receives the documented defaults.
- Unsupported grants, response types, authentication methods, or scopes return
  an RFC 7591 `invalid_client_metadata` response without creating a client.
- Missing, relative, fragmented, insecure remote HTTP, or otherwise invalid
  redirect URIs return an RFC 7591 `invalid_redirect_uri` response without
  creating a client.
- Unknown metadata does not prevent registration and is not echoed.
- Multiple redirect URIs are persisted as one atomic registration; a failure
  leaves neither a partial client nor partial redirect records.
- Existing CLI-created confidential clients, client-credentials tokens, PKCE
  authorization-code tokens, and loopback redirect matching remain compatible.
- The registration operation is excluded from generated MCP tools.
- Documentation includes discovery and registration examples plus the open
  registration operational warning.

## Migration and rollback

The implementation may add nullable or defaulted client metadata fields if
needed, but it must remain compatible with existing SQLite databases and
CLI-created clients. Schema generation must not require deleting existing
client or token data.

Rollback removes the endpoint and its discovery metadata. Dynamically
registered database records may remain inert and can be managed with the
existing client administration commands; rollback must not require destructive
data migration.

## Verification

Automated verification will cover schemas, service/repository atomicity,
endpoint success and RFC error responses, discovery metadata, compatibility
with existing OAuth flows, and exclusion from MCP operations. Run the
repository's full test, formatting, lint, and type-check commands that exist on
the merged base.

The smoke test will start the service against a disposable local SQLite
database, discover `/register`, dynamically register a public loopback client,
complete the existing S256 PKCE authorization-code exchange, and call a
protected weather endpoint using inputs and safety constraints supplied by the
user at the smoke-test gate.
