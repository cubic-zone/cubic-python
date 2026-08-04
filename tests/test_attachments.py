"""Attachments: upload/retrieve/delete, and files bound to file-typed variables."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from conftest import body_of, cube_success_body, make_async_client, make_client

from cubic import Attachment, CubicError


def attachment_body(**overrides) -> dict:
    body = {
        "id": "att_a1B2c3D4e5F6g7",
        "filename": "report.pdf",
        "media_type": "application/pdf",
        "tier": "native",
        "size_bytes": 1234,
        "sha256": "ab" * 32,
        "status": "active",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    body.update(overrides)
    return body


# ---- upload -------------------------------------------------------------------


def test_upload_from_path(tmp_path, request_log):
    f = tmp_path / "notes.md"
    f.write_bytes(b"# hi")

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(201, json=attachment_body(filename="notes.md", tier="text"))

    with make_client(handler) as client:
        att = client.attachments.upload(f)
    assert isinstance(att, Attachment)
    assert att.tier == "text"
    req = request_log[0]
    assert req.url.path == "/v1/attachments"
    assert b'filename="notes.md"' in req.content
    assert b"# hi" in req.content


def test_upload_bytes_requires_filename():
    with make_client(lambda r: httpx.Response(201, json=attachment_body())) as client:
        with pytest.raises(CubicError, match="filename"):
            client.attachments.upload(b"raw bytes")
        att = client.attachments.upload(b"raw bytes", filename="doc.pdf")
        assert att.id.startswith("att_")


def test_upload_rejects_text_mode_file(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello")
    with make_client(lambda r: httpx.Response(201, json=attachment_body())) as client:
        with pytest.raises(CubicError, match="binary mode"):
            client.attachments.upload(open(f))  # noqa: SIM115 - deliberate text mode


def test_retrieve_and_delete(request_log):
    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json=attachment_body(status="expired"))

    with make_client(handler) as client:
        att = client.attachments.retrieve("att_a1B2c3D4e5F6g7")
        assert att.status == "expired"
        client.attachments.delete("att_a1B2c3D4e5F6g7")
    assert [r.method for r in request_log] == ["GET", "DELETE"]
    assert request_log[1].url.path == "/v1/attachments/att_a1B2c3D4e5F6g7"


# ---- files as variable values ---------------------------------------------------


def test_path_variable_is_uploaded_and_bound(tmp_path, request_log):
    """A file-typed variable takes a file directly; the SDK uploads it and sends
    the id, so callers hand a cube a file the way they hand it a string."""
    f = tmp_path / "context.md"
    f.write_bytes(b"# context")

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        if request.url.path == "/v1/attachments":
            return httpx.Response(201, json=attachment_body(filename="context.md", tier="text"))
        return httpx.Response(200, json=cube_success_body())

    with make_client(handler) as client:
        client.completions.create("cbe_a1B2c3D4e5F6g7", {"notes": f, "tone": "brisk"})

    assert request_log[0].url.path == "/v1/attachments"
    sent = body_of(request_log[1])["variables"]
    assert sent == {"notes": "att_a1B2c3D4e5F6g7", "tone": "brisk"}


def test_bytes_tuple_variable_is_uploaded(tmp_path, request_log):
    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        if request.url.path == "/v1/attachments":
            return httpx.Response(201, json=attachment_body(id="att_ZZZZZZZZZZZZZZ"))
        return httpx.Response(200, json=cube_success_body())

    with make_client(handler) as client:
        client.completions.create("cbe_a1B2c3D4e5F6g7", {"doc": ("inline.txt", b"hello")})

    assert b'filename="inline.txt"' in request_log[0].content
    assert body_of(request_log[1])["variables"] == {"doc": "att_ZZZZZZZZZZZZZZ"}


def test_already_uploaded_file_is_not_re_uploaded(request_log):
    """An Attachment (or its id) is reused as-is — re-running with the same
    document costs no second upload."""
    att = Attachment.model_validate(attachment_body())

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(200, json=cube_success_body())

    with make_client(handler) as client:
        client.completions.create("cbe_a1B2c3D4e5F6g7", {"a": att, "b": att.id})

    assert [r.url.path for r in request_log] == ["/v1/completions"]
    assert body_of(request_log[0])["variables"] == {"a": att.id, "b": att.id}


def test_plain_strings_are_never_treated_as_files(request_log):
    """A string is the variable's text, never a filename — nothing is uploaded
    by accident."""

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(200, json=cube_success_body())

    with make_client(handler) as client:
        client.completions.create("cbe_a1B2c3D4e5F6g7", {"topic": "report.pdf"})

    assert [r.url.path for r in request_log] == ["/v1/completions"]
    assert body_of(request_log[0])["variables"] == {"topic": "report.pdf"}


def test_batch_items_bind_their_own_files(tmp_path, request_log):
    a = tmp_path / "a.md"
    a.write_bytes(b"# a")
    uploaded = iter(["att_AAAAAAAAAAAAAA", "att_BBBBBBBBBBBBBB"])

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        if request.url.path == "/v1/attachments":
            return httpx.Response(201, json=attachment_body(id=next(uploaded)))
        return httpx.Response(200, json=cube_success_body())

    with make_client(handler) as client:
        client.completions.create(
            "cbe_a1B2c3D4e5F6g7",
            [
                {"id": "1", "variables": {"doc": a}},
                {"id": "2", "variables": {"doc": ("b.md", b"# b")}},
            ],
        )

    sent = body_of(request_log[-1])["variables"]
    assert [item["variables"]["doc"] for item in sent] == [
        "att_AAAAAAAAAAAAAA",
        "att_BBBBBBBBBBBBBB",
    ]


def test_retired_attachments_argument_explains_the_migration():
    with make_client(lambda r: httpx.Response(200, json=cube_success_body())) as client:
        with pytest.raises(CubicError, match="file variable"):
            client.completions.create("cbe_a1B2c3D4e5F6g7", attachments=["att_a1B2c3D4e5F6g7"])


def test_create_omits_attachments_when_absent(request_log):
    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(200, json=cube_success_body())

    with make_client(handler) as client:
        client.completions.create("cbe_a1B2c3D4e5F6g7")
    assert "attachments" not in body_of(request_log[0])


def test_bound_files_survive_the_polycube_retry(request_log):
    """The polycube 422-retry drops client_request_id but keeps the variables —
    including their bound file ids, so the file isn't uploaded twice."""

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        if "client_request_id" in json.loads(request.content):
            return httpx.Response(
                422,
                json={
                    "detail": "polycube — not applicable: client_request_id",
                    "error_code": "polycube_field_not_applicable",
                },
            )
        return httpx.Response(
            200,
            json={
                "request_id": "11111111-1111-1111-1111-111111111111",
                "chain_id": "cbe_a1B2c3D4e5F6g7",
                "status": "success",
                "final_output": "done",
                "segments": [],
                "attempt_errors": [],
                "overall_metrics": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_cost": 0.0,
                    "response_time_ms": 5,
                    "success": True,
                },
            },
        )

    with make_client(handler) as client:
        result = client.completions.create(
            "cbe_a1B2c3D4e5F6g7", {"doc": "att_a1B2c3D4e5F6g7"}
        )
    assert result.content == "done"
    assert len(request_log) == 2
    retry = body_of(request_log[1])
    assert "client_request_id" not in retry
    assert retry["variables"] == {"doc": "att_a1B2c3D4e5F6g7"}


# ---- async parity ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_upload_and_bind(tmp_path, request_log):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.7")

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        if request.url.path == "/v1/attachments":
            return httpx.Response(201, json=attachment_body())
        return httpx.Response(200, json=cube_success_body())

    async with make_async_client(handler) as client:
        # An explicit upload, then reuse by object.
        att = await client.attachments.upload(f)
        await client.completions.create("cbe_a1B2c3D4e5F6g7", {"contract": att})
        # And the one-step form: the path is uploaded for you.
        await client.completions.create("cbe_a1B2c3D4e5F6g7", {"contract": f})

    assert body_of(request_log[1])["variables"] == {"contract": att.id}
    assert request_log[2].url.path == "/v1/attachments"
    assert body_of(request_log[3])["variables"] == {"contract": att.id}
