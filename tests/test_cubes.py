from __future__ import annotations

import httpx
import pytest

import cubic
from conftest import error_envelope, make_client

CUBE_BODY = {
    "cube_id": "cbe_plaincube0001",
    "title": "Support reply drafter",
    "completion_type": "fallback",
    "callback_url": None,
    "parameters": {"temperature": 0.4},
    "merge_responses": False,
    "current_version": 7,
    "version_number": 7,
    "system_instructions": "You are a courteous support agent for ACME.",
    "user_prompt": "Draft a reply to {{customer_name}} about {{issue}}.",
    "variables": {"customer_name": {"type": "string"}, "issue": {"type": "string"}},
    "functions": [],
    "response_format": None,
    "response_format_source": "none",
    "models": [
        {"provider": "openai", "model_name": "gpt-4o-mini", "rank": 0, "role": "primary"},
        {"provider": "anthropic", "model_name": "claude-sonnet-4-5", "rank": 1, "role": "fallback"},
    ],
}


def test_retrieve_returns_full_definition():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/cubes/cbe_plaincube0001"
        assert "version_number" not in dict(request.url.params)
        return httpx.Response(200, json=CUBE_BODY)

    with make_client(handler) as client:
        cube = client.cubes.retrieve("cbe_plaincube0001")

    assert cube.system_instructions == "You are a courteous support agent for ACME."
    assert cube.user_prompt.startswith("Draft a reply")
    assert set(cube.variables) == {"customer_name", "issue"}
    assert cube.models[0].provider == "openai" and cube.models[0].rank == 0
    assert cube.current_version == 7
    assert cube.kind == "cube"  # forward-compat default


def test_retrieve_pinned_version_sends_query_param():
    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {"version_number": "5"}
        return httpx.Response(200, json={**CUBE_BODY, "version_number": 5})

    with make_client(handler) as client:
        cube = client.cubes.retrieve("cbe_plaincube0001", version=5)
    assert cube.version_number == 5


def test_unknown_or_foreign_id_raises_cube_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return error_envelope(404, "Cube not found", "cube_not_found")

    with make_client(handler) as client:
        with pytest.raises(cubic.CubeNotFoundError, match="may not own it"):
            client.cubes.retrieve("cbe_someoneelses1")


def test_missing_version_raises_version_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return error_envelope(404, "Version 99 not found", "version_not_found")

    with make_client(handler) as client:
        with pytest.raises(cubic.VersionNotFoundError):
            client.cubes.retrieve("cbe_plaincube0001", version=99)
