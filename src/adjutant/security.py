import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from adjutant.errors import DomainError


def canonical_bytes(value: Any) -> bytes:
    """Canonical wire encoding; money enters this format as decimal strings."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    result = hashlib.scrypt(
        password.encode(), salt=salt, n=32768, r=8, p=1, maxmem=64 * 1024 * 1024
    )
    return f"scrypt${salt.hex()}${result.hex()}"


def password_matches(password: str, encoded: str) -> bool:
    algorithm, salt, expected = encoded.split("$")
    if algorithm != "scrypt":
        raise ValueError("Unsupported password hash")
    actual = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=64 * 1024 * 1024
    )
    return hmac.compare_digest(actual.hex(), expected)


def session_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_signing_key(path: Path) -> None:
    """Provision once. Existing approval authority must never be silently replaced."""
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    with path.open("xb") as handle:
        handle.write(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    path.chmod(0o600)


class ApprovalSigner:
    """Ed25519 signatures over immutable, brand-bound approval claims."""

    def __init__(self, path: Path) -> None:
        self._key = Ed25519PrivateKey.from_private_bytes(path.read_bytes())
        self.public_bytes = self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.key_id = hashlib.sha256(self.public_bytes).hexdigest()[:24]

    def sign(self, claims: dict[str, Any]) -> bytes:
        return self._key.sign(canonical_bytes(claims))


def verify_claims(
    public_key: bytes, claims: dict[str, Any], signature: bytes, *, now: datetime | None = None
) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, canonical_bytes(claims))
    except (InvalidSignature, ValueError) as exc:
        raise DomainError("InvalidSignature", "Approval signature is invalid.", 403) from exc
    current = int((now or datetime.now(UTC)).timestamp())
    issued, expires = claims.get("iat"), claims.get("exp")
    if (
        type(issued) is not int
        or type(expires) is not int
        or issued > current
        or expires <= current
        or not 0 < expires - issued <= 72 * 3600
    ):
        raise DomainError("TokenExpired", "Approval is outside its absolute validity window.", 403)


def public_key_text(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")
