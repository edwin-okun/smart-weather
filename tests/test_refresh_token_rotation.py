import base64
import hashlib
import unittest
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.models.auth import AccessToken, ApiClient, RefreshToken
from app.security import hash_token


class RefreshTokenRotationTests(unittest.TestCase):
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
        registration = self.client.post(
            "/register",
            json={
                "redirect_uris": ["app.example:/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
            },
        )
        self.assertEqual(registration.status_code, 201, registration.text)
        self.registered = registration.json()

    def _exchange_code(self, verifier: str) -> dict:
        challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode("ascii")).digest()
            )
            .rstrip(b"=")
            .decode("ascii")
        )
        redirect_uri = self.registered["redirect_uris"][0]
        authorization = self.client.get(
            "/authorize",
            params={
                "client_id": self.registered["client_id"],
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "weather:read",
            },
            follow_redirects=False,
        )
        self.assertEqual(authorization.status_code, 302, authorization.text)
        code = parse_qs(urlsplit(authorization.headers["location"]).query)["code"][0]
        token = self.client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": self.registered["client_id"],
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
        self.assertEqual(token.status_code, 200, token.text)
        return token.json()

    def _refresh(self, refresh_token: str):
        return self.client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self.registered["client_id"],
                "refresh_token": refresh_token,
            },
        )

    def _access_status(self, access_token: str) -> int:
        # Tokens only carry weather:read, so an authenticated request to the
        # history endpoint yields 403 while a revoked token yields 401.
        return self.client.get(
            "/weather/history",
            headers={"Authorization": f"Bearer {access_token}"},
        ).status_code

    def _access_token_row(self, access_token: str) -> AccessToken:
        async def fetch() -> AccessToken:
            return await AccessToken.get(token_hash=hash_token(access_token))

        return self.client.portal.call(fetch)

    def _refresh_token_row(self, refresh_token: str) -> RefreshToken:
        async def fetch() -> RefreshToken:
            return await RefreshToken.get(token_hash=hash_token(refresh_token))

        return self.client.portal.call(fetch)

    def test_replay_revokes_only_its_own_family(self) -> None:
        session_a = self._exchange_code(
            "session-a-verifier-0123456789012345678901234567890123"
        )
        session_b = self._exchange_code(
            "session-b-verifier-0123456789012345678901234567890123"
        )

        rotated_a = self._refresh(session_a["refresh_token"])
        self.assertEqual(rotated_a.status_code, 200, rotated_a.text)
        rotated_a = rotated_a.json()

        replay = self._refresh(session_a["refresh_token"])
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json()["detail"], "invalid_grant")

        # Session A's whole family is revoked: both access tokens and the
        # rotated refresh token.
        self.assertEqual(self._access_status(session_a["access_token"]), 401)
        self.assertEqual(self._access_status(rotated_a["access_token"]), 401)
        self.assertIsNotNone(
            self._refresh_token_row(rotated_a["refresh_token"]).revoked_at
        )
        self.assertEqual(self._refresh(rotated_a["refresh_token"]).status_code, 400)

        # Session B for the same client is untouched.
        self.assertIsNone(self._access_token_row(session_b["access_token"]).revoked_at)
        self.assertEqual(self._access_status(session_b["access_token"]), 403)
        rotated_b = self._refresh(session_b["refresh_token"])
        self.assertEqual(rotated_b.status_code, 200, rotated_b.text)
        self.assertEqual(self._access_status(rotated_b.json()["access_token"]), 403)

    def test_rotation_carries_family_id_to_new_access_token(self) -> None:
        session = self._exchange_code(
            "family-verifier-0123456789012345678901234567890123456"
        )
        initial_refresh = self._refresh_token_row(session["refresh_token"])
        initial_access = self._access_token_row(session["access_token"])
        self.assertIsNotNone(initial_refresh.family_id)
        self.assertEqual(initial_access.family_id, initial_refresh.family_id)

        rotated = self._refresh(session["refresh_token"])
        self.assertEqual(rotated.status_code, 200, rotated.text)
        rotated = rotated.json()

        new_access = self._access_token_row(rotated["access_token"])
        new_refresh = self._refresh_token_row(rotated["refresh_token"])
        self.assertEqual(new_access.family_id, initial_refresh.family_id)
        self.assertEqual(new_refresh.family_id, initial_refresh.family_id)


if __name__ == "__main__":
    unittest.main()
