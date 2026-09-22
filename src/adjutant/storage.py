"""Local immutable object storage with tenant paths and verified content hashes."""

import hashlib
import os
import re
import tempfile
from pathlib import Path
from uuid import UUID


class ObjectStore:
    """A persistent filesystem backend; identical writes resolve to the same object."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()

    def _path(self, brand_id: UUID, key: str) -> Path:
        if re.fullmatch(r"[a-f0-9]{64}", key) is None:
            raise ValueError("Invalid object key")
        path = self.root / str(UUID(str(brand_id))) / key
        if any(item.is_symlink() or item.is_junction() for item in (path.parent, path)):
            raise ValueError("Object paths cannot be symbolic links or junctions")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Object path escapes storage root")
        return path

    def put(self, brand_id: UUID, content: bytes) -> str:
        key = hashlib.sha256(content).hexdigest()
        path = self._path(brand_id, key)
        path.parent.mkdir(exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".upload-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return key

    def read(self, brand_id: UUID, key: str) -> bytes:
        content = self._path(brand_id, key).read_bytes()
        if hashlib.sha256(content).hexdigest() != key:
            raise ValueError("Stored object failed its content hash check")
        return content
