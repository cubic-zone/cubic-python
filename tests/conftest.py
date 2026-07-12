from __future__ import annotations

import json
import uuid
from typing import Callable

import httpx
import pytest

from cubic import AsyncCubic, Cubic


def make_client(handler: Callable[[httpx.Request], httpx.Response], **kwargs) -> Cubic:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    kwargs.setdefault("backoff_base", 0.0)  # no real sleeping in tests
    return Cubic(api_key="mxk_test_key", base_url="http://testserver", http_client=http_client, **kwargs)


def make_async_client(handler: Callable[[httpx.Request], httpx.Response], **kwargs) -> AsyncCubic:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    kwargs.setdefault("backoff_base", 0.0)  # no real sleeping in tests
    return AsyncCubic(api_key="mxk_test_key", base_url="http://testserver", http_client=http_client, **kwargs)


@pytest.fixture
def request_log() -> list[httpx.Request]:
    return []


def body_of(request: httpx.Request) -> dict:
    return json.loads(request.content.decode())


def metrics(**overrides) -> dict:
    base = {
        "input_tokens": 10,
        "output_tokens": 20,
        "total_cost": 0.001,
        "response_time_ms": 350,
        "success": True,
        "credits_charged": 2,
    }
    base.update(overrides)
    return base


def cube_success_body(content: str = "Hello Ada", **overrides) -> dict:
    body = {
        "request_id": str(uuid.uuid4()),
        "status": "success",
        "completions": [
            {
                "completion_id": str(uuid.uuid4()),
                "content": content,
                "model_used": "gpt-4o-mini",
                "provider_used": "openai",
                "completion_type": "FALLBACK",
                "is_winner": True,
                "metrics": metrics(),
            }
        ],
        "attempts": [],
        "attempt_errors": [],
        "overall_metrics": metrics(),
    }
    body.update(overrides)
    return body


def polycube_success_body(final_output="chained result", **overrides) -> dict:
    body = {
        "request_id": str(uuid.uuid4()),
        "chain_id": "cbe_polycube000001",
        "status": "success",
        "segments": [
            {
                "node_key": "node_1",
                "cube_id": "cbe_nodecube00001",
                "version_number": 3,
                "status": "success",
                "output": "intermediate",
                "metrics": metrics(),
            },
            {
                "node_key": "node_2",
                "cube_id": "cbe_nodecube00002",
                "version_number": 1,
                "status": "success",
                "output": final_output,
                "metrics": metrics(),
            },
        ],
        "final_output": final_output,
        "attempt_errors": [],
        "overall_metrics": metrics(),
    }
    body.update(overrides)
    return body


def error_envelope(status: int, detail: str, error_code: str | None = None, **extra) -> httpx.Response:
    body: dict = {"detail": detail}
    if error_code is not None:
        body["error_code"] = error_code
    body.update(extra)
    return httpx.Response(status, json=body, headers={"X-Request-ID": "req-test-123"})
