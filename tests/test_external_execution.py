"""Render + attach: Cubic's prompt management with your own inference.

The context manager is the interesting surface. Two properties it exists for:
it attaches on the way out so nobody has to remember to, and it attaches a
FAILURE when the block raises — a hand-rolled try/finally is exactly what gets
skipped, and without it external error rates would be indistinguishable from
renders nobody finished.
"""

from __future__ import annotations

import httpx
import pytest

import cubic
from conftest import body_of, error_envelope, make_async_client, make_client

RENDER_BODY = {
    "completion_id": "8e3e7268-80b1-4664-b5e4-6b175716c841",
    "prompt_id": "cbe_plaincube0001",
    "version_number": 7,
    "resolved_channel": "production",
    "completion_type": "fallback",
    "models": [
        {"provider": "openai", "model_name": "gpt-5", "rank": 0, "role": "primary"},
        {"provider": "anthropic", "model_name": "claude-sonnet-5", "rank": 1, "role": "fallback"},
    ],
    "parameters": {"temperature": 0.2},
    "response_format": {"type": "object"},
    "response_format_source": "manual",
    "renders": [
        {
            "batch_item_id": None,
            "system_instructions": "You are terse.",
            "user_prompt": "Answer: why is the sky blue?",
            "messages": [
                {"role": "system", "content": "You are terse."},
                {"role": "user", "content": "Answer: why is the sky blue?"},
            ],
            "variables_used": {"q": "why is the sky blue?"},
        }
    ],
    "usage": {"credits_rated": 3, "functions_time_ms": 120, "events": []},
}


def batch_render_body() -> dict:
    return {
        **RENDER_BODY,
        "renders": [
            {
                "batch_item_id": bid,
                "system_instructions": "You are terse.",
                "user_prompt": f"Answer: {bid}",
                "messages": [{"role": "user", "content": f"Answer: {bid}"}],
                "variables_used": {},
            }
            for bid in ("a", "b")
        ],
    }


def record_body(**overrides) -> dict:
    return {
        "request_id": RENDER_BODY["completion_id"],
        "status": "success",
        "execution_mode": "external",
        "response_attached_at": "2026-08-09T10:00:00+00:00",
        **overrides,
    }


class FakeUsage:
    """An OpenAI-shaped usage object."""

    prompt_tokens = 1204
    completion_tokens = 88


# ---- render ----


def test_render_returns_everything_needed_to_reproduce_the_call():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/completions/render"
        assert body_of(request)["prompt_id"] == "cbe_plaincube0001"
        return httpx.Response(200, json=RENDER_BODY)

    with make_client(handler) as client:
        rendered = client.completions.render("cbe_plaincube0001", {"q": "why is the sky blue?"})

    assert rendered.user_prompt == "Answer: why is the sky blue?"
    assert rendered.system_instructions == "You are terse."
    # Fallback order is the cube's behaviour, so the whole stack comes back.
    assert [m.model_name for m in rendered.models] == ["gpt-5", "claude-sonnet-5"]
    assert rendered.model_name == "gpt-5"  # convenience for the common case
    assert rendered.provider == "openai"
    assert rendered.response_format == {"type": "object"}
    assert rendered.usage.credits_rated == 3


def test_scalar_accessors_refuse_a_batch_render():
    """Returning the first item's prompt would mean running one and attributing
    the answer to all of them."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=batch_render_body())

    with make_client(handler) as client:
        rendered = client.completions.render("cbe_plaincube0001", [{"id": "a", "variables": {}}])

    assert len(rendered.renders) == 2
    with pytest.raises(ValueError, match="batch render"):
        _ = rendered.messages


# ---- attach ----


def test_attach_maps_provider_usage_and_sends_scalar_form():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=record_body())

    with make_client(handler) as client:
        client.completions.attach(
            RENDER_BODY["completion_id"],
            output="Rayleigh scattering.",
            provider="openai",
            model_name="gpt-5",
            usage=FakeUsage(),
        )

    body = body_of(seen[0])
    assert seen[0].url.path == f"/v1/completions/{RENDER_BODY['completion_id']}/response"
    assert "items" not in body  # scalar form, not a one-element batch
    assert body["output"] == "Rayleigh scattering."
    # OpenAI's prompt_tokens/completion_tokens mapped client-side.
    assert body["input_tokens"] == 1204 and body["output_tokens"] == 88


def test_anthropic_usage_shape_maps_too():
    seen: list[httpx.Request] = []

    class AnthropicUsage:
        input_tokens = 40
        output_tokens = 9

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=record_body())

    with make_client(handler) as client:
        client.completions.attach("cid", output="ok", usage=AnthropicUsage())

    body = body_of(seen[0])
    assert body["input_tokens"] == 40 and body["output_tokens"] == 9


def test_unknown_usage_shape_stays_unknown():
    """Never a silent zero — 0 would drag down every Usage average."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=record_body())

    with make_client(handler) as client:
        client.completions.attach("cid", output="ok", usage=object())

    body = body_of(seen[0])
    assert "input_tokens" not in body or body["input_tokens"] is None


# ---- the context manager ----


def test_external_block_attaches_on_exit():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/render"):
            return httpx.Response(200, json=RENDER_BODY)
        return httpx.Response(200, json=record_body())

    with make_client(handler) as client:
        with client.completions.external("cbe_plaincube0001", {"q": "x"}) as run:
            assert run.messages[0]["role"] == "system"
            assert run.model_name == "gpt-5"
            run.output = "Rayleigh scattering."
            run.usage = FakeUsage()

    assert [r.url.path.split("/")[-1] for r in seen] == ["render", "response"]
    body = body_of(seen[1])
    assert body["output"] == "Rayleigh scattering."
    assert body["input_tokens"] == 1204


def test_external_block_attaches_the_failure_and_reraises():
    """The reason this is a context manager: nobody remembers try/finally, and a
    silent failure is indistinguishable from a render nobody attached to."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/render"):
            return httpx.Response(200, json=RENDER_BODY)
        return httpx.Response(200, json=record_body(status="error"))

    with make_client(handler) as client:
        with pytest.raises(RuntimeError, match="upstream 529"):
            with client.completions.external("cbe_plaincube0001", {"q": "x"}):
                raise RuntimeError("upstream 529")

    body = body_of(seen[1])
    assert body["status"] == "error"
    assert "upstream 529" in body["error"]


def test_batch_block_iterates_and_attaches_every_item_at_once():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/render"):
            return httpx.Response(200, json=batch_render_body())
        return httpx.Response(200, json=record_body())

    with make_client(handler) as client:
        with client.completions.external(
            "cbe_plaincube0001", [{"id": "a", "variables": {}}, {"id": "b", "variables": {}}]
        ) as batch:
            for item in batch:
                item.output = f"answer for {item.batch_item_id}"

    body = body_of(seen[1])
    assert {i["batch_item_id"]: i["output"] for i in body["items"]} == {
        "a": "answer for a",
        "b": "answer for b",
    }
    assert len(seen) == 2  # one attach for the whole batch, not one per item


def test_iterating_a_scalar_render_is_a_typeerror():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/render"):
            return httpx.Response(200, json=RENDER_BODY)
        return httpx.Response(200, json=record_body())

    with make_client(handler) as client:
        with pytest.raises(TypeError, match="scalar render"):
            with client.completions.external("cbe_plaincube0001", {"q": "x"}) as run:
                list(run)


def test_paid_plan_is_required():
    def handler(request: httpx.Request) -> httpx.Response:
        return error_envelope(
            403,
            "External execution requires a paid plan",
            "external_execution_requires_paid_plan",
        )

    with make_client(handler) as client:
        with pytest.raises(cubic.CubicError):
            client.completions.render("cbe_plaincube0001", {"q": "x"})


@pytest.mark.asyncio
async def test_async_block_does_the_same():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/render"):
            return httpx.Response(200, json=RENDER_BODY)
        return httpx.Response(200, json=record_body())

    async with make_async_client(handler) as client:
        async with client.completions.external("cbe_plaincube0001", {"q": "x"}) as run:
            run.output = "Rayleigh scattering."
            run.usage = FakeUsage()

    assert [r.url.path.split("/")[-1] for r in seen] == ["render", "response"]
    assert body_of(seen[1])["input_tokens"] == 1204


@pytest.mark.asyncio
async def test_async_block_attaches_failures_too():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/render"):
            return httpx.Response(200, json=RENDER_BODY)
        return httpx.Response(200, json=record_body(status="error"))

    async with make_async_client(handler) as client:
        with pytest.raises(RuntimeError):
            async with client.completions.external("cbe_plaincube0001", {"q": "x"}):
                raise RuntimeError("boom")

    assert body_of(seen[1])["status"] == "error"


# ---- decorator sugar ----


def test_external_handler_attaches_the_return_value():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/render"):
            return httpx.Response(200, json=RENDER_BODY)
        return httpx.Response(200, json=record_body())

    with make_client(handler) as client:

        @client.completions.external_handler("cbe_plaincube0001")
        def handle(run, *, q: str) -> str:
            assert run.model_name == "gpt-5"
            return f"answered {q}"

        assert handle(q="why") == "answered why"

    # The kwargs became the render's variables...
    assert body_of(seen[0])["variables"] == {"q": "why"}
    # ...and the return value became the attached output.
    assert body_of(seen[1])["output"] == "answered why"


def test_external_handler_still_attaches_failures():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/render"):
            return httpx.Response(200, json=RENDER_BODY)
        return httpx.Response(200, json=record_body(status="error"))

    with make_client(handler) as client:

        @client.completions.external_handler("cbe_plaincube0001")
        def handle(run, *, q: str) -> str:
            raise RuntimeError("provider down")

        with pytest.raises(RuntimeError, match="provider down"):
            handle(q="why")

    assert body_of(seen[1])["status"] == "error"


@pytest.mark.asyncio
async def test_async_external_handler():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/render"):
            return httpx.Response(200, json=RENDER_BODY)
        return httpx.Response(200, json=record_body())

    async with make_async_client(handler) as client:

        @client.completions.external_handler("cbe_plaincube0001")
        async def handle(run, *, q: str) -> str:
            return f"answered {q}"

        assert await handle(q="why") == "answered why"

    assert body_of(seen[1])["output"] == "answered why"


# ---- metadata: intent at render, history at attach ----


def test_render_metadata_and_attach_metadata_travel_separately():
    """Two moments, two fields, and the record hands both back unmerged."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/render"):
            return httpx.Response(200, json=RENDER_BODY)
        return httpx.Response(
            200,
            json=record_body(
                metadata={"step": "triage"},
                response_metadata={"region": "us-east-1"},
            ),
        )

    with make_client(handler) as client:
        client.completions.render("cbe_plaincube0001", {"q": "x"}, metadata={"step": "triage"})
        record = client.completions.attach(
            RENDER_BODY["completion_id"],
            output="Rayleigh scattering.",
            metadata={"region": "us-east-1", "retries": 2},
        )

    assert body_of(seen[0])["metadata"] == {"step": "triage"}
    attached = body_of(seen[1])
    assert attached["metadata"] == {"region": "us-east-1", "retries": 2}
    # Still the scalar form — metadata describes the attach, not an item.
    assert "items" not in attached
    assert record.metadata == {"step": "triage"}
    assert record.response_metadata == {"region": "us-east-1"}


def test_a_batch_attach_carries_metadata_alongside_its_items():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/render"):
            return httpx.Response(200, json=batch_render_body())
        return httpx.Response(200, json=record_body())

    with make_client(handler) as client:
        with client.completions.external(
            "cbe_plaincube0001", [{"id": "a", "variables": {}}, {"id": "b", "variables": {}}]
        ) as run:
            for item in run:
                item.output = f"answer for {item.batch_item_id}"
            run.metadata = {"region": "us-east-1"}

    body = body_of(seen[1])
    assert len(body["items"]) == 2
    assert body["metadata"] == {"region": "us-east-1"}


def test_the_block_reports_how_it_ran_even_when_it_raises():
    """Knowing which endpoint you hit matters most on the runs that failed."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/render"):
            return httpx.Response(200, json=RENDER_BODY)
        return httpx.Response(200, json=record_body(status="error"))

    with make_client(handler) as client:
        with pytest.raises(RuntimeError):
            with client.completions.external("cbe_plaincube0001", {"q": "x"}) as run:
                run.metadata = {"region": "eu-west-1"}
                raise RuntimeError("upstream 529")

    body = body_of(seen[1])
    assert body["status"] == "error"
    assert body["metadata"] == {"region": "eu-west-1"}
