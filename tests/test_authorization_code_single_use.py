import asyncio
import base64
import hashlib
import unittest
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.models.auth import AccessToken, ApiClient, AuthorizationCode, RefreshToken
from app.repositories.auth import get_authorization_code_by_hash, redeem_authorization_code
from app.security import hash_token, utc_now
from app.services.auth import issue_authorization_code_token

VERIFIER = "single-use-verifier-0123456789012345678901234567890"
REDIRECT_URI = "http://127.0.0.1:43123/callback"


def _challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )


async def _token_counts() -> tuple[int, int]:
    return await AccessToken.all().count(), await RefreshToken.all().count()


class AuthorizationCodeSingleUseTests(unittest.TestCase):
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
            json={"redirect_uris": ["http://127.0.0.1/callback"]},
        )
        self.assertEqual(registration.status_code, 201)
        self.client_id = registration.json()["client_id"]
        authorization = self.client.get(
            "/authorize",
            params={
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": REDIRECT_URI,
                "code_challenge": _challenge(VERIFIER),
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        self.assertEqual(authorization.status_code, 302)
        self.code = parse_qs(urlsplit(authorization.headers["location"]).query)[
            "code"
        ][0]

    def _exchange(self, **overrides: str):
        return self.client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "code": self.code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": VERIFIER,
                **overrides,
            },
        )

    def test_code_can_be_exchanged_only_once(self) -> None:
        first = self._exchange()
        second = self._exchange()

        self.assertEqual(first.status_code, 200)
        self.assertIn("refresh_token", first.json())
        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.json()["detail"], "invalid_grant")
        self.assertEqual(self.client.portal.call(_token_counts), (1, 1))

    def test_wrong_verifier_does_not_consume_code(self) -> None:
        wrong = self._exchange(
            code_verifier="wrong-verifier-012345678901234567890123456789012",
        )
        valid = self._exchange()

        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(wrong.json()["detail"], "invalid_grant")
        self.assertEqual(valid.status_code, 200)

    def test_concurrent_exchanges_yield_exactly_one_token(self) -> None:
        async def exchange() -> str:
            try:
                await issue_authorization_code_token(
                    grant_type="authorization_code",
                    client_id=self.client_id,
                    code=self.code,
                    redirect_uri=REDIRECT_URI,
                    code_verifier=VERIFIER,
                    client_secret=None,
                )
            except HTTPException as exc:
                return exc.detail
            return "ok"

        async def race() -> list[str]:
            return await asyncio.gather(*(exchange() for _ in range(5)))

        results = self.client.portal.call(race)

        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(results.count("invalid_grant"), 4)
        self.assertEqual(self.client.portal.call(_token_counts), (1, 1))

    def test_redeem_claims_code_only_once_from_stale_state(self) -> None:
        async def redeem_twice() -> tuple[bool, bool]:
            # Both callers load the code before either claims it, as two
            # concurrent requests would; the stale copies both show
            # consumed_at=None.
            first_copy = await get_authorization_code_by_hash(hash_token(self.code))
            second_copy = await get_authorization_code_by_hash(hash_token(self.code))
            now = utc_now()

            async def redeem(code: AuthorizationCode, suffix: str) -> bool:
                return await redeem_authorization_code(
                    authorization_code=code,
                    access_token_hash=hash_token(f"access-{suffix}"),
                    refresh_token_hash=hash_token(f"refresh-{suffix}"),
                    refresh_family_id=f"family-{suffix}",
                    scopes=["weather:read"],
                    access_expires_at=now + timedelta(minutes=5),
                    refresh_expires_at=now + timedelta(days=1),
                    redeemed_at=now,
                )

            return await redeem(first_copy, "a"), await redeem(second_copy, "b")

        first, second = self.client.portal.call(redeem_twice)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(self.client.portal.call(_token_counts), (1, 1))


if __name__ == "__main__":
    unittest.main()
