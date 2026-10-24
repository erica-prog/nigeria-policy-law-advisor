"""Where uploaded originals live (ADR-001 action 4).

Until now an upload was read, chunked, embedded and deleted. Nothing could be
re-embedded under a new model, re-chunked under a new parser, shown to the
lawyer as the source page, or analysed in bulk later. Originals are now kept
in a blob store keyed by matter and document name, and recorded in the
`documents` table with their sha256 and size.

`LocalBlobStore` keeps them under `ORIGINALS_DIR` (`data/originals` by
default, a mounted volume in the container). An S3-compatible store with the
same interface is the Phase 1 follow-up; callers only ever see `BlobStore`.
"""

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

_HASH_CHUNK = 1024 * 1024


class BlobKeyError(ValueError):
    """A key that would escape the store or is otherwise malformed."""


@dataclass(frozen=True)
class BlobInfo:
    key: str
    sha256: str
    size: int


def validate_key(key: str) -> str:
    """Keys are `<matter_id>/<document name>`: relative, no traversal, no empty parts."""
    parts = key.split("/")
    if not key or key.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise BlobKeyError(f"invalid blob key {key!r}")
    return key


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(_HASH_CHUNK):
            digest.update(block)
    return digest.hexdigest()


class BlobStore(Protocol):
    def put(self, key: str, source: Path) -> BlobInfo: ...
    def open(self, key: str) -> BinaryIO: ...
    def path_for(self, key: str) -> Path | None: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> None: ...


class LocalBlobStore:
    def __init__(self, root: Path):
        self._root = root.resolve()

    def _resolve(self, key: str) -> Path:
        validate_key(key)
        path = (self._root / key).resolve()
        if self._root not in path.parents:
            raise BlobKeyError(f"blob key {key!r} resolves outside the store")
        return path

    def put(self, key: str, source: Path) -> BlobInfo:
        """Copy `source` into the store under `key`, replacing any previous
        blob with that key, via a temp file and atomic replace."""
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        shutil.copyfile(source, tmp)
        tmp.replace(target)
        return BlobInfo(key=key, sha256=sha256_of(target), size=target.stat().st_size)

    def open(self, key: str) -> BinaryIO:
        return self._resolve(key).open("rb")

    def path_for(self, key: str) -> Path | None:
        path = self._resolve(key)
        return path if path.is_file() else None

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        path.unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        """Remove every blob under a matter (`"<matter_id>/"`)."""
        folder = self._resolve(prefix.rstrip("/"))
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)


def document_key(matter_id: str, name: str) -> str:
    return validate_key(f"{matter_id}/{name}")
