"""Batch results: .contents keyed by batch item id; .content refuses to guess."""

from __future__ import annotations

import uuid

import httpx
import pytest

import cubic

from conftest import cube_success_body, make_client, metrics


def batch_item(item_id: str, content: str, error: dict | None = None) -> dict:
    return {
        "completion_id": str(uuid.uuid4()),
        "content": content if error is None else None,
        "model_used": "gpt-4o-mini",
        "provider_used": "openai",
        "completion_type": "BATCH_ITEM",
        "is_winner": None,
        "batch_item_id": item_id,
        "metrics": metrics(success=error is None),
        "error": error,
    }


def batch_body(**overrides) -> dict:
    return cube_success_body(
        completions=[batch_item("a", "first"), batch_item("b", "second")],
        **overrides,
    )


def test_contents_maps_batch_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=batch_body())

    with make_client(handler) as client:
        result = client.completions.create(
            "cbe_batchcube0001",
            variables=[
                {"id": "a", "variables": {"x": "1"}},
                {"id": "b", "variables": {"x": "2"}},
            ],
        )
    assert result.is_batch
    assert result.contents == {"a": "first", "b": "second"}


def test_content_raises_on_batch():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=batch_body())

    with make_client(handler) as client:
        result = client.completions.create(
            "cbe_batchcube0001", variables=[{"id": "a", "variables": {}}]
        )
    with pytest.raises(cubic.CubicError, match="contents"):
        _ = result.content


def test_contents_raises_on_single():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=cube_success_body("solo"))

    with make_client(handler) as client:
        result = client.completions.create("cbe_plaincube00001", variables={})
    assert not result.is_batch
    assert result.content == "solo"  # unchanged for single runs
    with pytest.raises(cubic.CubicError, match="content"):
        _ = result.contents


def test_partial_batch_failed_items_absent_from_contents():
    err = {
        "stage": "llm_call",
        "error_code": "http_500",
        "message": "boom",
        "is_retryable": True,
    }
    body = cube_success_body(
        status="partial",
        completions=[batch_item("a", "first")],
        attempts=[batch_item("a", "first"), batch_item("b", "", error=err)],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with make_client(handler) as client:
        result = client.completions.create(
            "cbe_batchcube0001", variables=[{"id": "a", "variables": {}}]
        )
    assert result.is_partial
    assert result.contents == {"a": "first"}
    failed = {c.batch_item_id: c.error for c in result.attempts if c.error is not None}
    assert failed["b"].error_code == "http_500"
