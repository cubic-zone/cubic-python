from __future__ import annotations

import httpx
import pytest

import cubic
from conftest import body_of, cube_success_body, error_envelope, make_client


def test_missing_api_key_raises_helpfully(monkeypatch):
    monkeypatch.delenv("CUBIC_API_KEY", raising=False)
    with pytest.raises(cubic.CubicError, match="CUBIC_API_KEY"):
        cubic.Cubic()


def test_401_maps_to_authentication_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return error_envelope(401, "Invalid API key", "invalid_api_key")

    with make_client(handler) as client:
        with pytest.raises(cubic.AuthenticationError) as exc_info:
            client.completions.create("cbe_plaincube0001", variables={})
    assert exc_info.value.error_code == "invalid_api_key"
    assert exc_info.value.request_id == "req-test-123"


def test_403_maps_to_permission_denied():
    def handler(request: httpx.Request) -> httpx.Response:
        return error_envelope(
            403,
            "Subscribe to this Cube (placing it in one of your projects) to run it",
            "marketplace_subscription_required",
        )

    with make_client(handler) as client:
        with pytest.raises(cubic.PermissionDeniedError):
            client.completions.create("cbe_notmine000001", variables={})


def test_insufficient_credits_by_error_code_even_on_429():
    def handler(request: httpx.Request) -> httpx.Response:
        return error_envelope(
            429,
            "Insufficient credits (500 required)",
            "insufficient_credits",
            dimension="credits",
            required=500,
            balance=200,
            grace=1200,
            topup_allowed=True,
        )

    with make_client(handler) as client:
        with pytest.raises(cubic.InsufficientCreditsError) as exc_info:
            client.completions.create("cbe_plaincube0001", variables={})
    e = exc_info.value
    assert (e.required, e.balance, e.grace, e.topup_allowed) == (500, 200, 1200, True)


def test_capacity_429_is_retried_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429, json={"detail": "Server at capacity, retry shortly"}, headers={"Retry-After": "0"}
            )
        return httpx.Response(200, json=cube_success_body())

    with make_client(handler) as client:
        result = client.completions.create("cbe_plaincube0001", variables={})
    assert calls["n"] == 2
    assert result.status == "success"


def test_capacity_429_raises_rate_limit_after_retries_exhausted():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"detail": "Server at capacity, retry shortly"}, headers={"Retry-After": "0"}
        )

    with make_client(handler, max_retries=1) as client:
        with pytest.raises(cubic.RateLimitError) as exc_info:
            client.completions.create("cbe_plaincube0001", variables={})
    assert exc_info.value.retry_after == 0


def test_5xx_retried_only_when_idempotent():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"detail": "upstream unavailable"})
        return httpx.Response(200, json=cube_success_body())

    # create() auto-attaches client_request_id -> idempotent -> retried
    with make_client(handler) as client:
        result = client.completions.create("cbe_plaincube0001", variables={})
    assert calls["n"] == 2
    assert result.status == "success"

    # known polycube -> no idempotency key -> 503 is NOT retried
    calls["n"] = 0

    def polycube_handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"detail": "upstream unavailable"})

    with make_client(polycube_handler) as client:
        client._kind_cache["cbe_polycube000001"] = "polycube"
        with pytest.raises(cubic.InternalServerError):
            client.completions.create("cbe_polycube000001", variables={})
    assert calls["n"] == 1


def test_connect_error_always_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json=cube_success_body())

    with make_client(handler) as client:
        result = client.completions.create("cbe_plaincube0001", variables={})
    assert calls["n"] == 2
    assert result.status == "success"


def test_422_validation_list_is_flattened():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "type": "value_error",
                        "loc": ["body", "parameters"],
                        "msg": "parameters.temperature must be a number between 0 and 2",
                        "input": {"temperature": 9},
                    }
                ]
            },
        )

    with make_client(handler) as client:
        with pytest.raises(cubic.InvalidRequestError, match="temperature must be a number"):
            client.completions.create(
                "cbe_plaincube0001", variables={}, parameters={"temperature": 9}
            )


def test_504_deadline_maps_to_completion_timeout():
    body = cube_success_body(
        status="error",
        completions=[],
        attempt_errors=[
            {
                "stage": "llm_call",
                "error_code": "deadline_exceeded",
                "message": "Completion exceeded the server deadline",
            }
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(504, json=body)

    with make_client(handler) as client:
        with pytest.raises(cubic.CompletionTimeoutError, match="server deadline") as exc_info:
            client.completions.create("cbe_plaincube0001", variables={})
    assert exc_info.value.error_code == "deadline_exceeded"


def test_user_supplied_client_request_id_is_respected():
    def handler(request: httpx.Request) -> httpx.Response:
        assert body_of(request)["client_request_id"] == "11111111-1111-1111-1111-111111111111"
        return httpx.Response(200, json=cube_success_body())

    with make_client(handler) as client:
        client.completions.create(
            "cbe_plaincube0001",
            variables={},
            client_request_id="11111111-1111-1111-1111-111111111111",
        )
