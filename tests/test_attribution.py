"""Telling Cubic who is calling, and which workflow a call belongs to.

Both are advisory: nothing here changes what a run does, only how it is filed in
Logs and Usage. So the properties worth pinning are that the identity travels on
EVERY request (not just completions), that it never overrides something the
caller set deliberately, and that the workflow id survives the polycube retry
path — which strips the other caller-supplied id.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from conftest import (
    body_of,
    cube_success_body,
    error_envelope,
    make_async_client,
    make_client,
    polycube_success_body,
)


def _ok(seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=cube_success_body())

    return handler


# ---- identifying the application --------------------------------------------


def test_app_url_and_title_ride_every_request():
    """Not just completions: attribution is a property of the client, and a
    caller who set it should not find half their traffic unattributed."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=cube_success_body())

    with make_client(
        handler, app_url="https://app.example.com/checkout", app_title="Example Checkout"
    ) as client:
        client.completions.create("cbe_plaincube0001")
        client.models.list()

    assert len(seen) == 2
    for request in seen:
        assert request.headers["HTTP-Referer"] == "https://app.example.com/checkout"
        assert request.headers["X-Title"] == "Example Checkout"


def test_no_attribution_headers_when_the_app_is_not_named():
    """Absent, not empty: an empty header would attribute the run to a domain
    that does not parse, which reads as a bug rather than as "not stated"."""
    seen: list[httpx.Request] = []
    with make_client(_ok(seen)) as client:
        client.completions.create("cbe_plaincube0001")
    assert "HTTP-Referer" not in seen[0].headers
    assert "X-Title" not in seen[0].headers


def test_either_half_can_be_given_alone():
    """A title with no URL identifies nothing server-side, but refusing it here
    would be the SDK second-guessing a caller who may add the URL later."""
    seen: list[httpx.Request] = []
    with make_client(_ok(seen), app_url="https://example.com") as client:
        client.completions.create("cbe_plaincube0001")
    assert seen[0].headers["HTTP-Referer"] == "https://example.com"
    assert "X-Title" not in seen[0].headers


def test_default_headers_are_sent_and_beat_the_convenience_arguments():
    seen: list[httpx.Request] = []
    with make_client(
        _ok(seen),
        app_url="https://example.com",
        default_headers={"X-Title": "Explicit", "X-Trace-Id": "t-1"},
    ) as client:
        client.completions.create("cbe_plaincube0001")
    assert seen[0].headers["X-Trace-Id"] == "t-1"
    assert seen[0].headers["X-Title"] == "Explicit"
    assert seen[0].headers["HTTP-Referer"] == "https://example.com"


def test_a_per_call_header_still_wins_over_a_default():
    """Idempotency keys and the like are set per call; a client-wide default
    must not shadow them."""
    seen: list[httpx.Request] = []
    with make_client(_ok(seen), default_headers={"Idempotency-Key": "client-wide"}) as client:
        client.request(
            "POST", "/v1/completions", json_body={"prompt_id": "cbe_x"},
            extra_headers={"Idempotency-Key": "per-call"},
        )
    assert seen[0].headers["Idempotency-Key"] == "per-call"


def test_attribution_never_displaces_auth_or_user_agent():
    seen: list[httpx.Request] = []
    with make_client(_ok(seen), app_url="https://example.com") as client:
        client.completions.create("cbe_plaincube0001")
    assert seen[0].headers["Authorization"] == "Bearer mxk_test_key"
    assert seen[0].headers["User-Agent"].startswith("cubic-python/")


@pytest.mark.asyncio
async def test_the_async_client_attributes_identically():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=cube_success_body())

    async with make_async_client(
        handler, app_url="https://example.com", app_title="Async App"
    ) as client:
        await client.completions.create("cbe_plaincube0001")

    assert seen[0].headers["HTTP-Referer"] == "https://example.com"
    assert seen[0].headers["X-Title"] == "Async App"


# ---- grouping a workflow -----------------------------------------------------


def test_run_id_is_sent_on_the_body():
    seen: list[httpx.Request] = []
    run = uuid.uuid4()
    with make_client(_ok(seen)) as client:
        client.completions.create("cbe_plaincube0001", run_id=run)
    assert body_of(seen[0])["run_id"] == str(run)


def test_a_string_run_id_is_accepted():
    """Callers carry workflow ids as strings far more often than as UUIDs."""
    seen: list[httpx.Request] = []
    with make_client(_ok(seen)) as client:
        client.completions.create("cbe_plaincube0001", run_id="8b1f7d2e-0000-4000-8000-000000000001")
    assert body_of(seen[0])["run_id"] == "8b1f7d2e-0000-4000-8000-000000000001"


def test_run_id_is_absent_when_not_given():
    seen: list[httpx.Request] = []
    with make_client(_ok(seen)) as client:
        client.completions.create("cbe_plaincube0001")
    assert "run_id" not in body_of(seen[0])


def test_several_calls_can_share_one_run():
    seen: list[httpx.Request] = []
    run = uuid.uuid4()
    with make_client(_ok(seen)) as client:
        client.completions.create("cbe_plaincube0001", {"a": 1}, run_id=run)
        client.completions.create("cbe_plaincube0002", {"b": 2}, run_id=run)
    assert [body_of(r)["run_id"] for r in seen] == [str(run), str(run)]
    # Each call keeps its OWN request id — run_id groups them, it does not
    # make them the same request.
    ids = {body_of(r)["client_request_id"] for r in seen}
    assert len(ids) == 2


def test_run_id_survives_the_polycube_retry_that_strips_client_request_id():
    """The retry exists because a polycube refuses ``client_request_id``. It must
    drop only that: a polycube is exactly the case where grouping the parent and
    its nodes under one run matters most."""
    seen: list[httpx.Request] = []
    run = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "client_request_id" in body_of(request):
            return error_envelope(
                422,
                "cbe_polycube000001 is a polycube — not applicable: client_request_id",
                "polycube_field_not_applicable",
            )
        return httpx.Response(200, json=polycube_success_body())

    with make_client(handler) as client:
        result = client.completions.create("cbe_polycube000001", {"topic": "rates"}, run_id=run)

    assert len(seen) == 2, "expected the polycube retry"
    retried = body_of(seen[1])
    assert "client_request_id" not in retried
    assert retried["run_id"] == str(run)
    assert result.kind == "polycube"


@pytest.mark.asyncio
async def test_the_async_client_sends_run_id_too():
    seen: list[httpx.Request] = []
    run = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=cube_success_body())

    async with make_async_client(handler) as client:
        await client.completions.create("cbe_plaincube0001", run_id=run)

    assert body_of(seen[0])["run_id"] == str(run)


# ---- reading it back ---------------------------------------------------------


def test_a_record_exposes_its_run_and_application():
    from cubic.types import CompletionRecord

    run, app = str(uuid.uuid4()), str(uuid.uuid4())
    record = CompletionRecord.model_validate(
        {"request_id": str(uuid.uuid4()), "status": "success", "run_id": run, "application_id": app}
    )
    assert record.run_id == run
    assert record.application_id == app


def test_an_unattributed_record_reads_as_none_not_missing():
    from cubic.types import CompletionRecord

    record = CompletionRecord.model_validate(
        {"request_id": str(uuid.uuid4()), "status": "success"}
    )
    assert record.run_id is None and record.application_id is None
