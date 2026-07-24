"""The files resource: download a Binary Cube's generated outputs.

The presigned ``url`` on a :class:`~cubic.GeneratedFile` works for about an
hour; these helpers go through ``GET /v1/files/{id}`` with your API key, which
works for the file's whole 30-day retention. Test-mode stub files (data-URI
urls, no id) are decoded locally with no HTTP call.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Union

from .. import _exceptions as err
from ..types import GeneratedFile

if TYPE_CHECKING:
    from .._async_client import AsyncCubic
    from .._client import Cubic

FileRef = Union[str, GeneratedFile]

# Extension by media type for save() when the caller gives a directory.
_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif",
    "audio/mpeg": "mp3", "audio/wav": "wav", "audio/ogg": "ogg", "audio/flac": "flac",
    "audio/aac": "aac", "video/mp4": "mp4", "video/webm": "webm",
}


@dataclass
class DownloadedFile:
    """Raw bytes of a generated file plus what you need to use them."""

    file_id: str | None
    media_type: str
    data: bytes

    def save(self, path: str | PathLike) -> Path:
        """Write the bytes to ``path`` (a file path, or a directory — then the
        name is derived from the file id and media type). Returns the path."""
        p = Path(path)
        if p.is_dir():
            ext = _EXT.get(self.media_type, "bin")
            p = p / f"{self.file_id or 'generated'}.{ext}"
        p.write_bytes(self.data)
        return p


def _local_stub(file: GeneratedFile) -> DownloadedFile | None:
    """Decode a test-mode stub (data-URI url) locally; None for real files."""
    url = file.url or ""
    if not url.startswith("data:"):
        return None
    head, _, payload = url.partition(",")
    media = head[5:].split(";", 1)[0] or file.media_type
    try:
        return DownloadedFile(file_id=file.id, media_type=media, data=base64.b64decode(payload))
    except Exception as e:  # pragma: no cover - malformed stub
        raise err.CubicError(f"Could not decode the test-stub data URI: {e}") from e


def _file_id_of(file: FileRef) -> str:
    if isinstance(file, GeneratedFile):
        if file.id is None:
            raise err.CubicError(
                "This file has no id (a test-mode stub with a non-data URL?) — "
                "nothing to download."
            )
        return file.id
    return file


def _from_response(file_id: str, response) -> DownloadedFile:
    return DownloadedFile(
        file_id=response.headers.get("X-Cubic-File-Id") or file_id,
        media_type=(response.headers.get("Content-Type") or "application/octet-stream").split(";")[0],
        data=response.content,
    )


DOWNLOAD_DOC = """Fetch a generated file's bytes by ``fil_…`` id or
        :class:`~cubic.GeneratedFile`.

        Works for the file's whole 30-day retention (unlike the short-lived
        presigned ``url``); raises :class:`~cubic.NotFoundError` with code
        ``file_expired`` after that. Test-mode stubs are decoded locally.
        """

SAVE_DOC = """Download and write a generated file to disk; returns the written
        :class:`~pathlib.Path`. ``path`` may be a file path or an existing
        directory (the name is then derived from the file id and media type).
        """


class Files:
    def __init__(self, client: "Cubic") -> None:
        self._client = client

    def download(self, file: FileRef) -> DownloadedFile:
        if isinstance(file, GeneratedFile):
            stub = _local_stub(file)
            if stub is not None:
                return stub
        file_id = _file_id_of(file)
        response = self._client.request("GET", f"/v1/files/{file_id}", idempotent=True)
        return _from_response(file_id, response)

    download.__doc__ = DOWNLOAD_DOC

    def save(self, file: FileRef, path: str | PathLike) -> Path:
        return self.download(file).save(path)

    save.__doc__ = SAVE_DOC


class AsyncFiles:
    def __init__(self, client: "AsyncCubic") -> None:
        self._client = client

    async def download(self, file: FileRef) -> DownloadedFile:
        if isinstance(file, GeneratedFile):
            stub = _local_stub(file)
            if stub is not None:
                return stub
        file_id = _file_id_of(file)
        response = await self._client.request("GET", f"/v1/files/{file_id}", idempotent=True)
        return _from_response(file_id, response)

    download.__doc__ = DOWNLOAD_DOC

    async def save(self, file: FileRef, path: str | PathLike) -> Path:
        return (await self.download(file)).save(path)

    save.__doc__ = SAVE_DOC
