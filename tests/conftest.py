from __future__ import annotations

import json
import os
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
        "credits_rated": 2,
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


# --- Live mode -------------------------------------------------------------
#
# The suite above drives an `httpx.MockTransport`: the handler answers, and the
# assertions check what the client did with the answer. That proves the SDK
# against a fixture. `tests/test_live.py` proves it against a running service.
#
# Live tests are opt-in by environment, and BOTH variables are required:
#
#     CUBIC_BASE_URL=http://127.0.0.1:8010 CUBIC_API_KEY=mxk_… make live
#
# Requiring the base URL as well as the key is deliberate. A key alone is
# something a developer may well have exported for their own use, and picking
# it up implicitly would point this suite at the hosted API without anyone
# asking it to. Naming the deployment is what turns a key into consent.

LIVE_BASE_URL_ENV = "CUBIC_BASE_URL"
LIVE_API_KEY_ENV = "CUBIC_API_KEY"


def live_config() -> tuple[str, str] | None:
    """The live target, or ``None`` when this is not a live run."""
    base_url = os.environ.get(LIVE_BASE_URL_ENV)
    api_key = os.environ.get(LIVE_API_KEY_ENV)
    if not base_url or not api_key:
        return None
    return base_url, api_key


def live_skip_reason() -> str | None:
    """Why a live test cannot run here, or ``None`` when it can."""
    missing = [n for n in (LIVE_BASE_URL_ENV, LIVE_API_KEY_ENV) if not os.environ.get(n)]
    if not missing:
        return None
    return f"live run needs {' and '.join(missing)} in the environment"


def pytest_collection_modifyitems(config, items) -> None:
    """Skip `live`-marked tests cleanly when the environment does not name a
    deployment. A skip says "not asked for"; a failure would say "broken"."""
    reason = live_skip_reason()
    if reason is None:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def live_client():
    """A real-transport client pointed at the deployment the environment names."""
    config = live_config()
    if config is None:
        pytest.skip(live_skip_reason() or "not a live run")
    base_url, api_key = config
    with Cubic(api_key=api_key, base_url=base_url, backoff_base=0.0) as client:
        yield client
