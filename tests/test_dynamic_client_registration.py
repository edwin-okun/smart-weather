import base64
import hashlib
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from app import db
from app.main import app, mcp
from app.models.auth import ApiClient
from app.repositories.auth import create_public_api_client_with_redirect_uris
from app.services.auth import register_api_client


async def _client_count() -> int:
    return await ApiClient.all().count()


class DynamicClientRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db.TORTOISE_ORM["connections"]["default"] = "sqlite://:memory:"
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)

    def setUp(self) -> None:
        self.client.portal.call(ApiClient.all().delete)

    def test_metadata_advertises_registration_endpoint(self) -> None:
        response = self.client.get("/.well-known/oauth-authorization-server")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["registration_endpoint"],
            "http://testserver/register",
        )

    def test_registration_defaults_and_pkce_exchange(self) -> None:
        registration = self.client.post(
            "/register",
            json={
                "client_name": "Desktop weather client",
                "redirect_uris": [
                    "http://127.0.0.1/callback",
                    "http://127.0.0.1/callback",
                ],
                "unknown_metadata": "ignored",
            },
        )

        self.assertEqual(registration.status_code, 201)
        body = registration.json()
        self.assertNotIn("client_secret", body)
        self.assertNotIn("unknown_metadata", body)
        self.assertEqual(body["grant_types"], ["authorization_code"])
        self.assertEqual(body["response_types"], ["code"])
        self.assertEqual(body["token_endpoint_auth_method"], "none")
        self.assertEqual(body["redirect_uris"], ["http://127.0.0.1/callback"])
        self.assertEqual(
            body["scope"],
            "weather:read",
        )

        verifier = "dynamic-registration-verifier-01234567890123456789"
        challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode("ascii")).digest()
            )
            .rstrip(b"=")
            .decode("ascii")
        )
        redirect_uri = "http://127.0.0.1:43123/callback"
        authorization = self.client.get(
            "/authorize",
            params={
                "client_id": body["client_id"],
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "weather:read",
                "state": "test-state",
            },
            follow_redirects=False,
        )
        self.assertEqual(authorization.status_code, 302)
        redirect = urlsplit(authorization.headers["location"])
        query = parse_qs(redirect.query)
        self.assertEqual(query["state"], ["test-state"])

        token = self.client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": body["client_id"],
                "code": query["code"][0],
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
        self.assertEqual(token.status_code, 200)
        self.assertEqual(token.json()["scope"], "weather:read")
        history = self.client.get(
            "/weather/history",
            headers={
                "Authorization": f"Bearer {token.json()['access_token']}"
            },
        )
        self.assertEqual(history.status_code, 403)

    def test_invalid_metadata_and_redirects_do_not_create_clients(self) -> None:
        cases = [
            (
                {
                    "grant_types": ["client_credentials"],
                    "redirect_uris": ["app.example:/cb"],
                },
                "invalid_client_metadata",
            ),
            (
                {
                    "response_types": ["token"],
                    "redirect_uris": ["app.example:/cb"],
                },
                "invalid_client_metadata",
            ),
            (
                {
                    "token_endpoint_auth_method": "client_secret_basic",
                    "redirect_uris": ["app.example:/cb"],
                },
                "invalid_client_metadata",
            ),
            (
                {
                    "scope": "weather:write",
                    "redirect_uris": ["app.example:/cb"],
                },
                "invalid_client_metadata",
            ),
            (
                {"redirect_uris": ["http://example.com/callback"]},
                "invalid_redirect_uri",
            ),
            (
                {"redirect_uris": ["javascript:alert(document.domain)"]},
                "invalid_redirect_uri",
            ),
            ({}, "invalid_redirect_uri"),
        ]

        for payload, expected_error in cases:
            with self.subTest(payload=payload):
                response = self.client.post("/register", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], expected_error)

        self.assertEqual(self.client.portal.call(_client_count), 0)

    def test_malformed_and_non_json_requests_return_protocol_errors(self) -> None:
        malformed = self.client.post(
            "/register",
            content=b"{",
            headers={"Content-Type": "application/json"},
        )
        wrong_content_type = self.client.post(
            "/register",
            content=b'{"redirect_uris":["app.example:/callback"]}',
            headers={"Content-Type": "text/plain"},
        )

        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(malformed.json()["error"], "invalid_client_metadata")
        self.assertEqual(wrong_content_type.status_code, 400)
        self.assertEqual(
            wrong_content_type.json()["error"],
            "invalid_client_metadata",
        )

    def test_oversized_registration_body_is_rejected_without_persistence(
        self,
    ) -> None:
        response = self.client.post(
            "/register",
            content=b'{"padding":"' + (b"x" * (64 * 1024)) + b'"}',
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_client_metadata")
        self.assertEqual(self.client.portal.call(_client_count), 0)

    def test_redirect_scheme_allowlist_supports_known_editor_schemes(self) -> None:
        for redirect_uri in [
            "vscode://callback",
            "vscode-insiders://callback",
            "cursor://anysphere/oauth",
            "com.example.weather:/callback",
        ]:
            with self.subTest(redirect_uri=redirect_uri):
                response = self.client.post(
                    "/register",
                    json={"redirect_uris": [redirect_uri]},
                )
                self.assertEqual(response.status_code, 201, response.text)

        for redirect_uri in [
            "about:blank",
            "blob:https://evil.example/x",
            "intent://evil.example/callback",
            "view-source:https://evil.example",
            "ws://evil.example/callback",
        ]:
            with self.subTest(redirect_uri=redirect_uri):
                response = self.client.post(
                    "/register",
                    json={"redirect_uris": [redirect_uri]},
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json()["error"],
                    "invalid_redirect_uri",
                )

    def test_client_and_redirects_roll_back_together(self) -> None:
        async def create_with_failed_redirects() -> None:
            with patch(
                "app.repositories.auth.ApiClientRedirectUri.bulk_create",
                side_effect=RuntimeError("forced redirect failure"),
            ):
                await create_public_api_client_with_redirect_uris(
                    client_id="swc_atomic_test",
                    client_secret_hash="!",
                    name="Atomic test",
                    scopes=["weather:read"],
                    redirect_uris=["app.example:/callback"],
                )

        with self.assertRaises(RuntimeError):
            self.client.portal.call(create_with_failed_redirects)
        self.assertEqual(self.client.portal.call(_client_count), 0)

    def test_persistence_failure_returns_generic_500_without_partial_client(
        self,
    ) -> None:
        transport = self.client._transport
        original_raise_server_exceptions = transport.raise_server_exceptions
        transport.raise_server_exceptions = False
        try:
            with patch(
                "app.repositories.auth.ApiClientRedirectUri.bulk_create",
                side_effect=RuntimeError("sensitive forced failure"),
            ):
                response = self.client.post(
                    "/register",
                    json={"redirect_uris": ["app.example:/callback"]},
                )
        finally:
            transport.raise_server_exceptions = original_raise_server_exceptions

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("sensitive forced failure", response.text)
        self.assertEqual(self.client.portal.call(_client_count), 0)

    def test_registration_is_not_an_mcp_tool(self) -> None:
        self.assertNotIn("register_oauth_client", mcp.operation_map)

    def test_openapi_documents_registration_request_body(self) -> None:
        operation = self.client.get("/openapi.json").json()["paths"][
            "/register"
        ]["post"]
        request_body = operation["requestBody"]

        self.assertTrue(request_body["required"])
        schema = request_body["content"]["application/json"]["schema"]
        self.assertIn("redirect_uris", schema["properties"])
        self.assertIn("redirect_uris", schema["required"])

    def test_public_client_cannot_use_client_credentials(self) -> None:
        registration = self.client.post(
            "/register",
            json={
                "redirect_uris": ["app.example:/callback"],
                "scope": "weather:read",
            },
        ).json()

        token = self.client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": registration["client_id"],
                "client_secret": "guessed",
            },
        )
        self.assertEqual(token.status_code, 401)

    def test_narrow_registration_cannot_authorize_broader_scope(self) -> None:
        registration = self.client.post(
            "/register",
            json={
                "redirect_uris": ["https://client.example/callback"],
                "scope": "weather:read",
            },
        ).json()

        exact_redirect = self.client.get(
            "/authorize",
            params={
                "client_id": registration["client_id"],
                "response_type": "code",
                "redirect_uri": "https://client.example/callback",
                "code_challenge": "challenge",
                "code_challenge_method": "S256",
                "scope": "weather:history:read",
            },
            follow_redirects=False,
        )
        mismatched_redirect = self.client.get(
            "/authorize",
            params={
                "client_id": registration["client_id"],
                "response_type": "code",
                "redirect_uri": "https://client.example/other",
                "code_challenge": "challenge",
                "code_challenge_method": "S256",
                "scope": "weather:read",
            },
            follow_redirects=False,
        )

        self.assertEqual(exact_redirect.status_code, 400)
        self.assertEqual(exact_redirect.json()["detail"], "invalid_scope")
        self.assertEqual(mismatched_redirect.status_code, 400)
        self.assertEqual(
            mismatched_redirect.json()["detail"],
            "invalid_redirect_uri",
        )

    def test_existing_confidential_client_creation_still_returns_secret(self) -> None:
        created = self.client.portal.call(
            register_api_client,
            "Existing CLI client",
            ["weather:read"],
        )

        self.assertTrue(created.client_id.startswith("swc_"))
        self.assertTrue(created.client_secret.startswith("sws_"))
        token = self.client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": created.client_id,
                "client_secret": created.client_secret,
                "scope": "weather:read",
            },
        )
        self.assertEqual(token.status_code, 200)


if __name__ == "__main__":
    unittest.main()
