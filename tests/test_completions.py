from __future__ import annotations

import uuid

import httpx
import pytest

import cubic
from conftest import (
    body_of,
    cube_success_body,
    error_envelope,
    make_client,
    polycube_success_body,
)


def test_create_success_maps_fields_and_auth():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=cube_success_body())

    with make_client(handler) as client:
        result = client.completions.create(
            "cbe_plaincube0001",
            variables={"customer_name": "Ada"},
            version=5,
            parameters={"temperature": 0.7},
        )

    request = seen[0]
    assert request.url.path == "/v1/completions"
    assert request.headers["Authorization"] == "Bearer mxk_test_key"
    body = body_of(request)
    assert body["prompt_id"] == "cbe_plaincube0001"  # cube_id -> prompt_id
    assert body["version_number"] == 5
    assert body["variables"] == {"customer_name": "Ada"}
    uuid.UUID(body["client_request_id"])  # auto idempotency key attached

    assert isinstance(result, cubic.CompletionResult)
    assert result.kind == "cube"
    assert result.content == "Hello Ada"
    assert result.metrics.credits_charged == 2
    assert not result.is_partial and not result.is_queued


def test_partial_status_returns_result_without_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=cube_success_body(status="partial"))

    with make_client(handler) as client:
        result = client.completions.create("cbe_plaincube0001", variables={})
    assert result.is_partial
    assert result.content == "Hello Ada"


def test_queued_status_returned_for_async_path():
    body = cube_success_body(status="queued", completions=[], task_id=str(uuid.uuid4()))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json=body)

    with make_client(handler) as client:
        result = client.completions.create(
            "cbe_plaincube0001", variables={}, callback_url="https://example.com/hook"
        )
    assert result.is_queued
    assert result.task_id is not None


def test_polycube_fallback_drops_auto_idempotency_key_and_caches_kind():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = body_of(request)
        seen.append(body)
        if "client_request_id" in body:
            return error_envelope(
                422,
                f"{body['prompt_id']} is a polycube — not applicable: client_request_id",
                "polycube_field_not_applicable",
            )
        return httpx.Response(200, json=polycube_success_body())

    with make_client(handler) as client:
        result = client.completions.create("cbe_polycube000001", variables={"topic": "x"})
        assert isinstance(result, cubic.PolycubeResult)
        assert result.kind == "polycube"
        assert result.content == "chained result"
        assert result.cube_id == "cbe_polycube000001"
        assert [s.node_key for s in result.segments] == ["node_1", "node_2"]

        # kind is cached: the next call must not send client_request_id at all
        client.completions.create("cbe_polycube000001", variables={"topic": "y"})

    assert len(seen) == 3  # 422 probe + resend + second call (no probe)
    assert "client_request_id" not in seen[1]
    assert "client_request_id" not in seen[2]


def test_polycube_rejection_of_caller_fields_is_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return error_envelope(
            422,
            "cbe_polycube000001 is a polycube — not applicable: models, client_request_id",
            "polycube_field_not_applicable",
        )

    with make_client(handler) as client:
        with pytest.raises(cubic.InvalidRequestError) as exc_info:
            client.completions.create(
                "cbe_polycube000001",
                variables={},
                models=[{"provider": "openai", "model_name": "gpt-4o"}],
            )
    assert exc_info.value.error_code == "polycube_field_not_applicable"


def test_pipeline_missing_variable_raises_typed_error():
    body = cube_success_body(
        status="error",
        completions=[],
        attempt_errors=[
            {
                "stage": "variable_validation",
                "error_code": "content_error",
                "message": "Required variable 'customer_name' was not provided",
                "is_retryable": False,
            }
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with make_client(handler) as client:
        with pytest.raises(cubic.MissingVariableError) as exc_info:
            client.completions.create("cbe_plaincube0001", variables={})
    assert exc_info.value.variable_name == "customer_name"
    assert exc_info.value.result is not None
    assert exc_info.value.result.status == "error"


def test_pipeline_prompt_resolution_raises_cube_not_found():
    body = cube_success_body(
        status="error",
        completions=[],
        attempt_errors=[
            {
                "stage": "prompt_resolution",
                "error_code": "not_found",
                "message": "Prompt not found",
            }
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with make_client(handler) as client:
        with pytest.raises(cubic.CubeNotFoundError):
            client.completions.create("cbe_missing000001", variables={})


def test_pipeline_provider_failure_raises_provider_error():
    body = cube_success_body(
        status="error",
        completions=[],
        attempt_errors=[
            {
                "stage": "llm_call",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "error_code": "circuit_open",
                "message": "openai temporarily unavailable (circuit open after repeated failures)",
                "is_retryable": True,
            }
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with make_client(handler) as client:
        with pytest.raises(cubic.ProviderError) as exc_info:
            client.completions.create("cbe_plaincube0001", variables={})
    assert exc_info.value.attempt_errors[0].is_retryable is True


def test_polycube_node_error_is_surfaced_with_node_context():
    body = polycube_success_body(status="error", final_output=None)
    body["segments"][1].update(
        {
            "status": "error",
            "output": None,
            "error": {
                "stage": "llm_call",
                "error_code": "http_500",
                "message": "Internal server error from upstream",
                "is_retryable": True,
            },
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with make_client(handler) as client:
        with pytest.raises(cubic.ProviderError) as exc_info:
            client.completions.create("cbe_polycube000001", variables={})
    assert "node 'node_2'" in str(exc_info.value)


def test_retrieve_returns_record_for_either_kind():
    rid = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v1/completions/{rid}"
        return httpx.Response(
            200,
            json={
                "request_id": rid,
                "cube_id": "cbe_polycube000001",
                "kind": "polycube",
                "status": "success",
                "segments": [],
                "final_output": "done",
                "total_cost": 0.01,
                "created_at": "2026-07-09T00:00:00+00:00",
            },
        )

    with make_client(handler) as client:
        record = client.completions.retrieve(rid)
    assert record.kind == "polycube"
    assert record.final_output == "done"


def test_retrieve_defaults_kind_to_cube():
    rid = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"request_id": rid, "status": "success", "prompt_id": "uuid-here", "attempts": []},
        )

    with make_client(handler) as client:
        record = client.completions.retrieve(rid)
    assert record.kind == "cube"
