"""Binary outputs: GeneratedFile parsing on results, files.download/save."""

from __future__ import annotations

import base64

import httpx
import pytest
from conftest import make_async_client, make_client

from cubic import CompletionResult, CubicError, GeneratedFile

PNG = b"\x89PNG-fake-bytes"


def file_meta(**overrides) -> dict:
    meta = {
        "id": "fil_a1B2c3D4e5F6g7",
        "kind": "image",
        "media_type": "image/png",
        "size_bytes": len(PNG),
        "sha256": "ab" * 32,
        "url": "https://s3.example/presigned?sig=x",
        "expires_at": "2026-08-23T00:00:00+00:00",
    }
    meta.update(overrides)
    return meta


def binary_result_body(*contents) -> dict:
    return {
        "request_id": "11111111-1111-1111-1111-111111111111",
        "status": "success",
        "completions": [
            {
                "completion_id": f"22222222-2222-2222-2222-22222222222{i}",
                "content": c,
                "completion_type": "fallback",
                "is_winner": i == 0,
                "metrics": {
                    "input_tokens": 1, "output_tokens": 0, "total_cost": 0.0,
                    "response_time_ms": 5, "success": True,
                },
            }
            for i, c in enumerate(contents)
        ],
        "attempt_errors": [],
        "overall_metrics": {
            "input_tokens": 1, "output_tokens": 0, "total_cost": 0.0,
            "response_time_ms": 5, "success": True,
        },
    }


# ---- result accessors ----------------------------------------------------------


def test_result_file_and_files():
    r = CompletionResult.model_validate(binary_result_body({"file": file_meta()}))
    assert isinstance(r.file, GeneratedFile)
    assert r.file.id == "fil_a1B2c3D4e5F6g7"
    assert r.file.kind == "image" and not r.file.is_expired
    assert [f.id for f in r.files] == ["fil_a1B2c3D4e5F6g7"]


def test_result_file_raises_on_multiple_and_none_on_text():
    multi = CompletionResult.model_validate(
        binary_result_body({"file": file_meta()}, {"file": file_meta(id="fil_ZZZZZZZZZZZZZZ")})
    )
    assert len(multi.files) == 2
    with pytest.raises(CubicError, match="result.files"):
        _ = multi.file

    text = CompletionResult.model_validate(binary_result_body("plain text"))
    assert text.file is None and text.files == []

    # {"files": [...]} shape (one attempt, several artifacts).
    many = CompletionResult.model_validate(
        binary_result_body({"files": [file_meta(), file_meta(id="fil_YYYYYYYYYYYYYY")]})
    )
    assert [f.id for f in many.files] == ["fil_a1B2c3D4e5F6g7", "fil_YYYYYYYYYYYYYY"]


def test_expired_meta_reads_expired():
    f = GeneratedFile.model_validate({**file_meta(), "status": "expired", "url": None})
    assert f.is_expired


# ---- download / save -----------------------------------------------------------


def test_download_by_id_and_save(tmp_path, request_log):
    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(
            200,
            content=PNG,
            headers={"Content-Type": "image/png", "X-Cubic-File-Id": "fil_a1B2c3D4e5F6g7"},
        )

    with make_client(handler) as client:
        got = client.files.download("fil_a1B2c3D4e5F6g7")
        assert got.data == PNG and got.media_type == "image/png"
        assert request_log[0].url.path == "/v1/files/fil_a1B2c3D4e5F6g7"

        # save() into a directory derives the filename from id + media type.
        path = client.files.save(GeneratedFile.model_validate(file_meta()), tmp_path)
    assert path.name == "fil_a1B2c3D4e5F6g7.png"
    assert path.read_bytes() == PNG


def test_download_stub_decodes_locally(request_log):
    stub = GeneratedFile.model_validate(
        {
            "id": None,
            "kind": "image",
            "media_type": "image/png",
            "test_stub": True,
            "url": "data:image/png;base64," + base64.b64encode(b"STUB").decode(),
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        request_log.append(request)
        raise AssertionError("stub download must not hit the API")

    with make_client(handler) as client:
        got = client.files.download(stub)
    assert got.data == b"STUB" and got.media_type == "image/png"
    assert request_log == []


def test_download_idless_file_raises():
    idless = GeneratedFile.model_validate(
        {"id": None, "media_type": "image/png", "url": "https://host/x.png"}
    )
    with make_client(lambda r: httpx.Response(200)) as client:
        with pytest.raises(CubicError, match="no id"):
            client.files.download(idless)


def test_expired_download_maps_to_not_found():
    from cubic import NotFoundError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"detail": "This generated file has expired", "error_code": "file_expired"},
        )

    with make_client(handler) as client:
        with pytest.raises(NotFoundError) as e:
            client.files.download("fil_a1B2c3D4e5F6g7")
        assert e.value.error_code == "file_expired"


@pytest.mark.asyncio
async def test_async_download_and_result_parse(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v1/files/"):
            return httpx.Response(200, content=PNG, headers={"Content-Type": "image/png"})
        return httpx.Response(200, json=binary_result_body({"file": file_meta()}))

    async with make_async_client(handler) as client:
        result = await client.completions.create("cbe_a1B2c3D4e5F6g7")
        assert result.file is not None
        path = await client.files.save(result.file, tmp_path / "out.png")
    assert path.read_bytes() == PNG
