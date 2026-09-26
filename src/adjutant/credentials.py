"""Authenticated envelope encryption with one randomly generated key per brand."""

import os
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg import Connection

from adjutant.db import one


def provision_master_key(path: Path) -> None:
    """Create local wrapping authority once; never overwrite an existing key."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if len(path.read_bytes()) != 32:
            raise ValueError(
                "Credential master key must contain exactly 32 bytes"
            ) from None
        return
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(AESGCM.generate_key(bit_length=256))
        handle.flush()
        os.fsync(handle.fileno())


def seal(key: bytes, value: bytes, context: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, value, context)


def unseal(key: bytes, value: bytes, context: bytes) -> bytes:
    return AESGCM(key).decrypt(value[:12], value[12:], context)


class CredentialStore:
    """Use inside a caller-owned tenant transaction; plaintext never enters SQL or logs."""

    def __init__(self, master_key_path: Path) -> None:
        self._master_key_path = master_key_path

    def _key(self, conn: Connection, brand_id: UUID, *, create: bool) -> bytes:
        master = self._master_key_path.read_bytes()
        if len(master) != 32:
            raise ValueError("Credential master key must contain exactly 32 bytes")
        context = f"adjutant:tenant-key:v1:{brand_id}".encode()
        if create:
            wrapped = seal(master, AESGCM.generate_key(bit_length=256), context)
            conn.execute(
                "INSERT INTO tenant_secret_key(brand_id,wrapped_key) VALUES(%s,%s) "
                "ON CONFLICT (brand_id) DO NOTHING",
                (brand_id, wrapped),
            )
        row = one(
            conn,
            "SELECT wrapped_key FROM tenant_secret_key WHERE brand_id=%s",
            (brand_id,),
        )
        return unseal(master, bytes(row["wrapped_key"]), context)

    def write(self, conn: Connection, brand_id: UUID, name: str, value: str) -> None:
        if not value or len(value.encode()) > 65536:
            raise ValueError("Credential must contain 1 to 65536 UTF-8 bytes")
        key = self._key(conn, brand_id, create=True)
        context = f"adjutant:credential:v1:{brand_id}:{name}".encode()
        conn.execute(
            "INSERT INTO tenant_secret(brand_id,name,ciphertext) VALUES(%s,%s,%s) "
            "ON CONFLICT (brand_id,name) DO UPDATE "
            "SET ciphertext=excluded.ciphertext,updated_at=now()",
            (brand_id, name, seal(key, value.encode(), context)),
        )

    def read(self, conn: Connection, brand_id: UUID, name: str) -> str:
        key = self._key(conn, brand_id, create=False)
        row = one(
            conn,
            "SELECT ciphertext FROM tenant_secret WHERE brand_id=%s AND name=%s",
            (brand_id, name),
        )
        context = f"adjutant:credential:v1:{brand_id}:{name}".encode()
        return unseal(key, bytes(row["ciphertext"]), context).decode()
