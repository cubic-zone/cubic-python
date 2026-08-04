"""The attachments resource: upload files once, reference them by ``att_…`` id.

Uploaded bytes live for 7 days and the id is reusable across runs in that window
(re-running with the same document costs no re-upload, and a document converted
by ``<<READ::…>>`` is converted — and charged — once).

A file reaches a cube as the value of a **file-typed variable**, not through a
separate attachments list: the cube declares ``contract`` as a file, and the
request binds an id to it. ``completions.create`` accepts a
:class:`~pathlib.Path` or ``(filename, bytes)`` directly as that value and
uploads it for you.
"""

from __future__ import annotations

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

# What a file-typed variable's value may be: an already-uploaded attachment (or
# its id), a path, or (filename, bytes).
FileValue = Union[str, Attachment, PathLike, "tuple[str, bytes]"]

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


RETIRED_ATTACHMENTS_MESSAGE = (
    "attachments= is no longer supported. Declare a file variable on the cube "
    "(type 'file') and pass the file as that variable's value:\n"
    "    client.completions.create(cube_id, {'contract': Path('lease.pdf')})\n"
    "The prompt decides what happens to it — bare {{contract}} places the file, "
    "<<READ::{{contract}}>> reads it as text, <<TRANSCRIBE::{{contract}}>> "
    "transcribes it."
)


def is_file_value(value: Any) -> bool:
    """Whether a variable's value is a file the SDK should upload for you.

    Deliberately narrow: a plain string is never treated as a file (it is the
    variable's actual text), so nothing is uploaded by accident.
    """
    if isinstance(value, (Attachment, PathLike)):
        return True
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], (bytes, bytearray))
    )


def _file_variables(variables: Any) -> list[tuple[dict, str, Any]]:
    """Every ``(mapping, name, value)`` in a request whose value is a file —
    covering the batch form, where each item carries its own variables."""
    if isinstance(variables, dict):
        sets = [variables]
    elif isinstance(variables, list):
        sets = [
            item["variables"]
            for item in variables
            if isinstance(item, dict) and isinstance(item.get("variables"), dict)
        ]
    else:
        return []
    return [
        (mapping, name, value)
        for mapping in sets
        for name, value in mapping.items()
        if is_file_value(value)
    ]


def _upload_args(value: Any) -> tuple[Any, str | None]:
    if isinstance(value, tuple):
        return value[1], value[0]
    return value, None


def bind_file_variables(variables: Any, upload) -> Any:
    """Replace file-valued variables with their ``att_`` ids, uploading as needed.

    Lets a caller hand a cube a file the same way they hand it a string —
    ``{"contract": Path("lease.pdf")}`` — while the wire format stays what the
    API expects. An :class:`Attachment` (or an ``att_`` id string) is used
    as-is, so re-running with the same document costs no re-upload.
    """
    pending = _file_variables(variables)
    if not pending:
        return variables
    for mapping, name, value in pending:
        if isinstance(value, Attachment):
            mapping[name] = value.id
            continue
        file, filename = _upload_args(value)
        mapping[name] = upload(file, filename=filename).id
    return variables


async def abind_file_variables(variables: Any, upload) -> Any:
    """Async twin of :func:`bind_file_variables`."""
    pending = _file_variables(variables)
    if not pending:
        return variables
    for mapping, name, value in pending:
        if isinstance(value, Attachment):
            mapping[name] = value.id
            continue
        file, filename = _upload_args(value)
        mapping[name] = (await upload(file, filename=filename)).id
    return variables


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
