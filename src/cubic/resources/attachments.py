"""The attachments resource: upload files once, reference them by ``att_…`` id.

Uploaded bytes live for 7 days; the id is reusable across runs in that window
(re-running with the same document costs no re-upload, and server-side text
extraction for Office files is cached per attachment). ``completions.create``
also accepts :class:`~pathlib.Path` / ``(filename, bytes)`` entries directly —
those are sent inline (base64) without a separate upload; prefer an explicit
``upload()`` when the same file backs more than one run.
"""

from __future__ import annotations

import base64
import re
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from .. import _exceptions as err
from ..types import Attachment

if TYPE_CHECKING:
    from .._async_client import AsyncCubic
    from .._client import Cubic

_ATTACHMENT_ID_RE = re.compile(r"^att_[A-Za-z0-9]{14}$")

# What completions.create accepts in its attachments= list.
AttachmentInput = Union[str, Attachment, PathLike, "tuple[str, bytes]"]

UPLOAD_DOC = """Upload one file and get its reusable ``att_…`` id.

        ``file`` may be a path (``str``/``Path``), raw ``bytes`` (then pass
        ``filename=``), or an open binary file object. The server sniffs the
        real type from the bytes (supported: PDF, PNG/JPG/WEBP/GIF, MD/TXT/
        RTF/SVG, DOCX/PPTX/XLSX; ≤50MB) and returns the handling ``tier`` on
        the :class:`~cubic.types.Attachment`.
        """


def read_upload(file: Any, filename: str | None) -> tuple[str, bytes]:
    """Normalize upload() input to (filename, bytes)."""
    if isinstance(file, (str, PathLike)):
        path = Path(file)
        return filename or path.name, path.read_bytes()
    if isinstance(file, bytes):
        if not filename:
            raise err.CubicError("Pass filename=... when uploading raw bytes")
        return filename, file
    if hasattr(file, "read"):
        data = file.read()
        if isinstance(data, str):
            raise err.CubicError("Open attachment files in binary mode ('rb')")
        name = filename or getattr(file, "name", None)
        return (Path(name).name if name else None) or "attachment", data
    raise err.CubicError(
        f"Unsupported upload input {type(file).__name__}: pass a path, bytes, "
        "or a binary file object"
    )


def build_attachment_entries(attachments: list[AttachmentInput]) -> list[Any]:
    """Translate the SDK's attachments= list to wire entries.

    ``att_…`` id strings and :class:`Attachment` objects pass by reference;
    :class:`~pathlib.Path` and ``(filename, bytes)`` entries go inline as
    base64 (stored server-side exactly like an upload). Plain strings that are
    not ids are rejected rather than guessed at — pass ``Path(...)`` for files.
    """
    entries: list[Any] = []
    for item in attachments:
        if isinstance(item, Attachment):
            entries.append(item.id)
        elif isinstance(item, str):
            if not _ATTACHMENT_ID_RE.match(item):
                raise err.CubicError(
                    f"'{item}' is not an attachment id (att_…). To attach a "
                    "file, pass pathlib.Path(...) or (filename, bytes), or "
                    "upload it first with client.attachments.upload()."
                )
            entries.append(item)
        elif isinstance(item, PathLike):
            path = Path(item)
            entries.append(
                {"filename": path.name, "data": base64.b64encode(path.read_bytes()).decode()}
            )
        elif isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], (bytes, bytearray)):
            entries.append(
                {"filename": str(item[0]), "data": base64.b64encode(bytes(item[1])).decode()}
            )
        else:
            raise err.CubicError(
                f"Unsupported attachment entry {type(item).__name__}: pass an "
                "att_… id, an Attachment, a pathlib.Path, or (filename, bytes)"
            )
    return entries


class Attachments:
    def __init__(self, client: "Cubic") -> None:
        self._client = client

    def upload(self, file: Any, *, filename: str | None = None) -> Attachment:
        name, data = read_upload(file, filename)
        response = self._client.request(
            "POST", "/v1/attachments", files={"file": (name, data)}
        )
        return Attachment.model_validate(response.json())

    upload.__doc__ = UPLOAD_DOC

    def retrieve(self, attachment_id: str) -> Attachment:
        """Fetch an attachment's metadata (``status`` flips to ``expired``
        once the bytes have been purged)."""
        response = self._client.request(
            "GET", f"/v1/attachments/{attachment_id}", idempotent=True
        )
        return Attachment.model_validate(response.json())

    def delete(self, attachment_id: str) -> None:
        """Expire the attachment now — its bytes are removed immediately."""
        self._client.request("DELETE", f"/v1/attachments/{attachment_id}", idempotent=True)


class AsyncAttachments:
    def __init__(self, client: "AsyncCubic") -> None:
        self._client = client

    async def upload(self, file: Any, *, filename: str | None = None) -> Attachment:
        name, data = read_upload(file, filename)
        response = await self._client.request(
            "POST", "/v1/attachments", files={"file": (name, data)}
        )
        return Attachment.model_validate(response.json())

    upload.__doc__ = UPLOAD_DOC

    async def retrieve(self, attachment_id: str) -> Attachment:
        """Fetch an attachment's metadata (``status`` flips to ``expired``
        once the bytes have been purged)."""
        response = await self._client.request(
            "GET", f"/v1/attachments/{attachment_id}", idempotent=True
        )
        return Attachment.model_validate(response.json())

    async def delete(self, attachment_id: str) -> None:
        """Expire the attachment now — its bytes are removed immediately."""
        await self._client.request(
            "DELETE", f"/v1/attachments/{attachment_id}", idempotent=True
        )
