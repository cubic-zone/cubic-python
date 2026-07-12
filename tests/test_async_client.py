"""AsyncCubic mirrors the sync client: same parsing, errors, retries, and
polycube auto-detection — just awaitable."""

from __future__ import annotations

import httpx
import pytest

import cubic
from cubic import AsyncCubic

from conftest import body_of, cube_success_body, error_envelope, make_async_client, polycube_success_body


async def test_create_success_returns_content():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/completions"
        assert request.headers["Authorization"] == "Bearer mxk_test_key"
        assert body_of(request)["prompt_id"] == "cbe_plaincube00001"
        return httpx.Response(200, json=cube_success_body("Hello async"))

    async with make_async_client(handler) as client:
        result = await client.completions.create("cbe_plaincube00001", variables={"name": "Ada"})
    assert result.kind == "cube"
    assert result.content == "Hello async"


async def test_polycube_auto_detection_and_kind_cache():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = body_of(request)
        calls.append(body)
        if "client_request_id" in body:
            return httpx.Response(
                422,
                json={
                    "detail": "client_request_id is not applicable to a polycube",
                    "error_code": "polycube_field_not_applicable",
                },
            )
        return httpx.Response(200, json=polycube_success_body())

    async with make_async_client(handler) as client:
        result = await client.completions.create("cbe_polycube000001", variables={"topic": "x"})
        assert result.kind == "polycube"
        assert result.content == "chained result"
        # second run: kind is cached, no idempotency key attached, single call
        await client.completions.create("cbe_polycube000001", variables={"topic": "y"})
    assert len(calls) == 3
    assert "client_request_id" not in calls[2]


async def test_capacity_429_is_retried():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(
                429, json={"detail": "Server at capacity, retry shortly"}, headers={"Retry-After": "0"}
            )
        return httpx.Response(200, json=cube_success_body())

    async with make_async_client(handler) as client:
        result = await client.completions.create("cbe_plaincube00001", variables={})
    assert result.status == "success"
    assert attempts["n"] == 2


async def test_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return error_envelope(401, "Invalid API key", "invalid_api_key")

    async with make_async_client(handler) as client:
        with pytest.raises(cubic.AuthenticationError):
            await client.completions.create("cbe_plaincube00001", variables={})


async def test_pipeline_error_raises_missing_variable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=cube_success_body(
                status="error",
                completions=[],
                attempt_errors=[
                    {
                        "stage": "variable_validation",
                        "error_code": "content_error",
                        "message": "Required variable 'name' was not provided",
                        "is_retryable": False,
                    }
                ],
            ),
        )

    async with make_async_client(handler) as client:
        with pytest.raises(cubic.MissingVariableError) as exc_info:
            await client.completions.create("cbe_plaincube00001", variables={})
    assert exc_info.value.variable_name == "name"


async def test_cubes_retrieve():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/cubes/cbe_plaincube00001"
        assert request.url.params.get("version_number") == "2"
        return httpx.Response(
            200,
            json={
                "cube_id": "cbe_plaincube00001",
                "title": "Greeter",
                "completion_type": "fallback",
                "current_version": 3,
                "version_number": 2,
                "system_instructions": "Be nice.",
                "user_prompt": "Say hello to {{name}}.",
                "variables": {"name": {"type": "string", "required": True}},
                "models": [{"provider": "openai", "model_name": "gpt-4o-mini", "rank": 0, "role": "primary"}],
            },
        )

    async with make_async_client(handler) as client:
        cube = await client.cubes.retrieve("cbe_plaincube00001", version=2)
    assert cube.system_instructions == "Be nice."
    assert cube.models[0].model_name == "gpt-4o-mini"


async def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("CUBIC_API_KEY", raising=False)
    with pytest.raises(cubic.CubicError, match="No API key"):
        AsyncCubic(api_key=None, base_url="http://testserver")
