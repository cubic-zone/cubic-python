"""wait(): polling for queued (callback) runs until the record is persisted."""

from __future__ import annotations

import httpx
import pytest

import cubic

from conftest import cube_success_body, make_async_client, make_client


def record_body(status: str = "success", **overrides) -> dict:
    body = {
        "request_id": "11111111-1111-1111-1111-111111111111",
        "status": status,
        "prompt_id": "22222222-2222-2222-2222-222222222222",
        "model_used": "gpt-4o-mini",
        "provider_used": "openai",
        "attempts": [],
        "input_tokens": 10,
        "output_tokens": 20,
        "total_cost": 0.001,
        "created_at": "2026-07-10T00:00:00+00:00",
    }
    body.update(overrides)
    return body


def not_found() -> httpx.Response:
    return httpx.Response(
        404, json={"detail": "Completion not found", "error_code": "completion_not_found"}
    )


def test_wait_polls_until_persisted():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return not_found()
        return httpx.Response(200, json=record_body())

    with make_client(handler) as client:
        record = client.completions.wait(
            "11111111-1111-1111-1111-111111111111", poll_interval=0.0
        )
    assert record.status == "success"
    assert calls["n"] == 3


def test_wait_raises_completion_error_on_errored_record():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=record_body(
                status="error",
                error_detail=[
                    {
                        "stage": "callback_delivery",
                        "error_code": "delivery_failed",
                        "message": "callback returned 500",
                        "is_retryable": False,
                    }
                ],
            ),
        )

    with make_client(handler) as client:
        with pytest.raises(cubic.CompletionError) as exc_info:
            client.completions.wait("11111111-1111-1111-1111-111111111111", poll_interval=0.0)
    assert exc_info.value.error_code == "delivery_failed"
    assert "callback returned 500" in str(exc_info.value)


def test_wait_times_out():
    def handler(request: httpx.Request) -> httpx.Response:
        return not_found()

    with make_client(handler) as client:
        with pytest.raises(cubic.WaitTimeoutError):
            client.completions.wait(
                "11111111-1111-1111-1111-111111111111", timeout=0.05, poll_interval=0.01
            )


def test_queued_result_wait_shortcut():
    """create() with a callback returns a queued result whose .wait() polls."""
    state = {"created": False, "polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            state["created"] = True
            return httpx.Response(
                202,
                json=cube_success_body(
                    status="queued",
                    completions=[],
                    request_id="11111111-1111-1111-1111-111111111111",
                    task_id="11111111-1111-1111-1111-111111111111",
                ),
            )
        state["polls"] += 1
        if state["polls"] < 2:
            return not_found()
        return httpx.Response(200, json=record_body())

    with make_client(handler) as client:
        result = client.completions.create(
            "cbe_plaincube00001", variables={"name": "Ada"}, callback_url="https://example.com/hook"
        )
        assert result.is_queued
        record = result.wait(poll_interval=0.0)
    assert record.status == "success"
    assert state["polls"] == 2


async def test_async_wait_and_shortcut():
    state = {"polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                json=cube_success_body(
                    status="queued",
                    completions=[],
                    request_id="11111111-1111-1111-1111-111111111111",
                ),
            )
        state["polls"] += 1
        if state["polls"] < 2:
            return not_found()
        return httpx.Response(200, json=record_body())

    async with make_async_client(handler) as client:
        result = await client.completions.create(
            "cbe_plaincube00001", variables={"name": "Ada"}, callback_url="https://example.com/hook"
        )
        assert result.is_queued
        record = await result.wait(poll_interval=0.0)
    assert record.status == "success"


def test_manual_result_has_no_waiter():
    from cubic.types import CompletionResult

    result = CompletionResult.model_validate(cube_success_body(status="queued", completions=[]))
    with pytest.raises(RuntimeError, match="wait\\(\\) is only available"):
        result.wait()
