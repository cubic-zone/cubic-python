"""Attachments: upload/retrieve/delete, and attachments= on completions.create."""

from __future__ import annotations

import base64
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


# ---- completions.create wiring -------------------------------------------------


def test_create_passes_ids_and_inlines_files(tmp_path, request_log):
    f = tmp_path / "context.md"
    f.write_bytes(b"# context")

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(200, json=cube_success_body())

    with make_client(handler) as client:
        client.completions.create(
            "cbe_a1B2c3D4e5F6g7",
            attachments=[
                "att_a1B2c3D4e5F6g7",
                Attachment.model_validate(attachment_body(id="att_ZZZZZZZZZZZZZZ")),
                f,
                ("inline.txt", b"hello"),
            ],
        )
    sent = body_of(request_log[0])["attachments"]
    assert sent[0] == "att_a1B2c3D4e5F6g7"
    assert sent[1] == "att_ZZZZZZZZZZZZZZ"
    assert sent[2] == {"filename": "context.md", "data": base64.b64encode(b"# context").decode()}
    assert sent[3] == {"filename": "inline.txt", "data": base64.b64encode(b"hello").decode()}


def test_create_rejects_non_id_string():
    with make_client(lambda r: httpx.Response(200, json=cube_success_body())) as client:
        with pytest.raises(CubicError, match="pathlib.Path"):
            client.completions.create("cbe_a1B2c3D4e5F6g7", attachments=["report.pdf"])


def test_create_omits_attachments_when_absent(request_log):
    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(200, json=cube_success_body())

    with make_client(handler) as client:
        client.completions.create("cbe_a1B2c3D4e5F6g7")
    assert "attachments" not in body_of(request_log[0])


def test_attachments_survive_polycube_retry(request_log):
    """The polycube 422-retry drops client_request_id but keeps attachments."""

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
            "cbe_a1B2c3D4e5F6g7", attachments=["att_a1B2c3D4e5F6g7"]
        )
    assert result.content == "done"
    assert len(request_log) == 2
    retry = body_of(request_log[1])
    assert "client_request_id" not in retry
    assert retry["attachments"] == ["att_a1B2c3D4e5F6g7"]


# ---- async parity ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_upload_and_create(tmp_path, request_log):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.7")

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        if request.url.path == "/v1/attachments":
            return httpx.Response(201, json=attachment_body())
        return httpx.Response(200, json=cube_success_body())

    async with make_async_client(handler) as client:
        att = await client.attachments.upload(f)
        await client.completions.create("cbe_a1B2c3D4e5F6g7", attachments=[att])
    assert body_of(request_log[1])["attachments"] == [att.id]
