import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime


SECRET_HASH_ALGORITHM = "pbkdf2_sha256"
SECRET_HASH_ITERATIONS = 310_000


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for token expiry comparisons."""
    return datetime.now(UTC)


def generate_client_id() -> str:
    """Generate a public OAuth client identifier with an app-specific prefix."""
    return f"swc_{secrets.token_urlsafe(24)}"


def generate_client_secret() -> str:
    """Generate a one-time client secret for confidential client credentials."""
    return f"sws_{secrets.token_urlsafe(40)}"


def generate_access_token() -> str:
    """Generate an opaque bearer token; only its SHA-256 hash is persisted."""
    return f"swat_{secrets.token_urlsafe(48)}"


def generate_authorization_code() -> str:
    """Generate a short-lived OAuth authorization code for PKCE exchange."""
    return f"swac_{secrets.token_urlsafe(32)}"


def generate_refresh_token() -> str:
    """Generate an opaque rotating refresh token."""
    return f"swrt_{secrets.token_urlsafe(48)}"


def generate_token_family_id() -> str:
    """Generate a non-secret identifier for one refresh-token family."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash opaque tokens and authorization codes for lookup without storing secrets."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_pkce_s256(code_verifier: str, code_challenge: str) -> bool:
    """Validate a PKCE S256 verifier against the stored code challenge."""
    try:
        verifier_bytes = code_verifier.encode("ascii")
    except UnicodeEncodeError:
        return False

    digest = hashlib.sha256(verifier_bytes).digest()
    actual_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(actual_challenge, code_challenge)


def hash_secret(secret: str) -> str:
    """Hash a client secret with PBKDF2 so plaintext secrets are never stored."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode("utf-8"),
        salt,
        SECRET_HASH_ITERATIONS,
    )
    encoded_salt = base64.urlsafe_b64encode(salt).decode("ascii")
    encoded_digest = base64.urlsafe_b64encode(digest).decode("ascii")
    return (
        f"{SECRET_HASH_ALGORITHM}${SECRET_HASH_ITERATIONS}$"
        f"{encoded_salt}${encoded_digest}"
    )


def verify_secret(secret: str, secret_hash: str) -> bool:
    """Constant-time verification for a plaintext client secret and stored hash."""
    try:
        algorithm, iterations, encoded_salt, encoded_digest = secret_hash.split("$", 3)
        if algorithm != SECRET_HASH_ALGORITHM:
            return False

        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        expected_digest = base64.urlsafe_b64decode(encoded_digest.encode("ascii"))
        actual_digest = hashlib.pbkdf2_hmac(
            "sha256",
            secret.encode("utf-8"),
            salt,
            int(iterations),
        )
    except (ValueError, TypeError):
        return False

    return hmac.compare_digest(actual_digest, expected_digest)
