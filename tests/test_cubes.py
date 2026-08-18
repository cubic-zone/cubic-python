from __future__ import annotations

import json

import httpx
import pytest

import cubic
from conftest import body_of, error_envelope, make_client

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


def test_retrieve_by_channel_sends_query_param():
    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {"channel": "staging"}
        return httpx.Response(200, json={**CUBE_BODY, "version_number": 9})

    with make_client(handler) as client:
        cube = client.cubes.retrieve("cbe_plaincube0001", channel="staging")
    # The response reports what the channel landed on, so no second lookup.
    assert cube.version_number == 9


def test_retrieve_suffix_sugar_is_left_to_the_server():
    """``cbe_…@staging`` is one identifier on both surfaces; the SDK forwards it
    rather than parsing it, so there is one implementation of the rule."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/cubes/cbe_plaincube0001@staging"
        assert "channel" not in dict(request.url.params)
        return httpx.Response(200, json=CUBE_BODY)

    with make_client(handler) as client:
        client.cubes.retrieve("cbe_plaincube0001@staging")


def test_retrieve_rejects_version_and_channel_together():
    """Refused client-side: the mistake surfaces without a round trip."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request should be made")

    with make_client(handler) as client:
        with pytest.raises(TypeError, match="not both"):
            client.cubes.retrieve("cbe_plaincube0001", version=5, channel="staging")


def test_unknown_channel_raises_channel_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return error_envelope(404, "Channel “nope” not found on this cube", "channel_not_found")

    with make_client(handler) as client:
        with pytest.raises(cubic.CubicError):
            client.cubes.retrieve("cbe_plaincube0001", channel="nope")


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


# ---- authoring: create / update / versions / rollback / test ----

VERSION_BODY = {
    "version_number": 2,
    "version": "1.0.1",
    "change_ratio": 0.018,
    "is_current": True,
    "created_at": "2026-07-22T10:00:00Z",
}


def test_create_sends_payload_and_auto_idempotency_key():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/v1/cubes"
        seen["idem"] = request.headers.get("Idempotency-Key")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(201, json=CUBE_BODY)

    with make_client(handler) as client:
        cube = client.cubes.create(
            "Support reply drafter",
            system_instructions="You are a courteous support agent for ACME.",
            user_prompt="Draft a reply to {{customer_name}} about {{issue}}.",
            models=[{"provider": "openai", "model_name": "gpt-4o-mini", "rank": 0}],
            project_id="prj_abcDEF123456xy",
        )

    assert cube.cube_id == "cbe_plaincube0001"
    assert seen["idem"]  # auto-generated when not supplied
    assert seen["body"]["title"] == "Support reply drafter"
    assert seen["body"]["project_id"] == "prj_abcDEF123456xy"
    assert seen["body"]["models"][0]["model_name"] == "gpt-4o-mini"
    assert "completion_type" not in seen["body"]  # omitted fields stay server-defaulted


def test_create_retry_replays_the_same_idempotency_key():
    keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers["Idempotency-Key"])
        if len(keys) == 1:
            return httpx.Response(500, json={"detail": "boom"})
        return httpx.Response(201, json=CUBE_BODY)

    with make_client(handler) as client:
        client.cubes.create("T", user_prompt="Hi {{x}}", idempotency_key="fixed-key-1")

    assert keys == ["fixed-key-1", "fixed-key-1"]  # the retry replays, never re-mints


def test_create_unknown_model_raises_invalid_request():
    def handler(request: httpx.Request) -> httpx.Response:
        return error_envelope(
            422, "Unknown models (not in the model catalog): openai/gpt-nope", "unknown_model"
        )

    with make_client(handler) as client:
        with pytest.raises(cubic.InvalidRequestError) as exc_info:
            client.cubes.create(
                "T",
                user_prompt="Hi {{x}}",
                models=[{"provider": "openai", "model_name": "gpt-nope", "rank": 0}],
            )
    assert exc_info.value.error_code == "unknown_model"


def test_update_patches_only_given_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path == "/v1/cubes/cbe_plaincube0001"
        assert json.loads(request.content.decode()) == {"title": "Renamed"}
        return httpx.Response(200, json={**CUBE_BODY, "title": "Renamed"})

    with make_client(handler) as client:
        cube = client.cubes.update("cbe_plaincube0001", title="Renamed")
    assert cube.title == "Renamed"


def test_test_run_sends_content_overrides_and_parses_result():
    from conftest import cube_success_body

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/cubes/cbe_plaincube0001/test"
        body = json.loads(request.content.decode())
        assert body["user_prompt"] == "Greet {{captain}}!"
        assert body["variables"] == {"captain": "Ada"}
        assert "system_instructions" not in body  # None = keep saved text
        return httpx.Response(200, json=cube_success_body("Ahoy Ada!"))

    with make_client(handler) as client:
        result = client.cubes.test(
            "cbe_plaincube0001",
            variables={"captain": "Ada"},
            user_prompt="Greet {{captain}}!",
        )
    assert result.status == "success"
    assert result.content == "Ahoy Ada!"


def test_test_run_pipeline_error_raises_typed_exception():
    from conftest import cube_success_body

    def handler(request: httpx.Request) -> httpx.Response:
        body = cube_success_body()
        body.update(
            status="error",
            completions=[],
            attempt_errors=[
                {
                    "stage": "variable_validation",
                    "message": "Required variable 'captain' missing",
                    "error_code": "content_error",
                }
            ],
        )
        return httpx.Response(200, json=body)

    with make_client(handler) as client:
        with pytest.raises(cubic.MissingVariableError) as exc_info:
            client.cubes.test("cbe_plaincube0001", user_prompt="Greet {{captain}}!")
    assert exc_info.value.variable_name == "captain"


def test_create_version_sends_snapshot_and_parses_semver():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/cubes/cbe_plaincube0001/versions"
        body = json.loads(request.content.decode())
        assert body["user_prompt"] == "Draft a courteous reply to {{customer_name}}."
        assert body["system_instructions"].startswith("You are")
        return httpx.Response(201, json=VERSION_BODY)

    with make_client(handler) as client:
        v = client.cubes.create_version(
            "cbe_plaincube0001",
            system_instructions="You are a courteous support agent for ACME.",
            user_prompt="Draft a courteous reply to {{customer_name}}.",
        )
    assert v.version == "1.0.1" and v.version_number == 2
    assert v.is_current is True
    assert 0 < v.change_ratio < 0.05


def test_versions_lists_history_newest_first():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200, json=[VERSION_BODY, {**VERSION_BODY, "version_number": 1, "version": "1.0.0", "is_current": False, "change_ratio": None}]
        )

    with make_client(handler) as client:
        versions = client.cubes.versions("cbe_plaincube0001")
    assert [v.version_number for v in versions] == [2, 1]
    assert versions[1].change_ratio is None  # v1 has no predecessor


def test_set_current_version_rolls_back():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/v1/cubes/cbe_plaincube0001/current-version"
        assert json.loads(request.content.decode()) == {"version_number": 1}
        return httpx.Response(200, json={**CUBE_BODY, "version_number": 1})

    with make_client(handler) as client:
        cube = client.cubes.set_current_version("cbe_plaincube0001", 1)
    assert cube.version_number == 1


async def test_async_create_and_version(request_log):
    from conftest import make_async_client

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        if request.url.path == "/v1/cubes":
            return httpx.Response(201, json=CUBE_BODY)
        return httpx.Response(201, json=VERSION_BODY)

    async with make_async_client(handler) as client:
        cube = await client.cubes.create("T", user_prompt="Hi {{x}}")
        v = await client.cubes.create_version(cube.cube_id, user_prompt="Hello {{x}}")

    assert cube.cube_id == "cbe_plaincube0001"
    assert v.version == "1.0.1"
    assert request_log[0].headers.get("Idempotency-Key")


# ---- variable declarations ------------------------------------------------------


def test_variable_helper_builds_declarations():
    from cubic import variable

    assert variable() == {"type": "string", "required": True}
    assert variable("file", description="The signed agreement") == {
        "type": "file",
        "required": True,
        "description": "The signed agreement",
    }
    assert variable("integer", required=False) == {"type": "integer", "required": False}


def test_create_sends_variable_declarations(request_log):
    from cubic import variable

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(201, json=CUBE_BODY)

    with make_client(handler) as client:
        client.cubes.create(
            "Contract reviewer",
            user_prompt="Review {{contract}} for {{focus}}",
            variables={
                "contract": variable("file", description="The signed agreement"),
                "focus": variable(required=False),
            },
        )

    sent = body_of(request_log[0])["variables"]
    assert sent["contract"]["type"] == "file"
    assert sent["focus"]["required"] is False


def test_create_version_sends_variable_declarations(request_log):
    """Declarations are versioned content, so they travel with create_version —
    not update, which is prompt-level only."""
    from cubic import variable

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(201, json=VERSION_BODY)

    with make_client(handler) as client:
        client.cubes.create_version(
            "cbe_a1B2c3D4e5F6g7",
            user_prompt="Review {{contract}}",
            variables={"contract": variable("file")},
        )

    assert body_of(request_log[0])["variables"] == {"contract": {"type": "file", "required": True}}


def test_variables_omitted_when_not_declared(request_log):
    """Undeclared placeholders are auto-detected server-side, so the SDK sends
    nothing rather than an empty schema that would look like 'no inputs'."""

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(201, json=CUBE_BODY)

    with make_client(handler) as client:
        client.cubes.create("Plain", user_prompt="Hello {{name}}")

    assert "variables" not in body_of(request_log[0])


# ---- output types -------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "number"}, "reason": {"type": "string"}},
    "required": ["score"],
}
STRUCTURED_BODY = {
    **CUBE_BODY,
    "response_format": SCHEMA,
    "response_format_source": "manual",
    "is_structured": True,
    "output_kind": "text",
}


def test_create_structured_sends_the_flag_and_the_schema():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = body_of(request)
        return httpx.Response(201, json=STRUCTURED_BODY)

    with make_client(handler) as client:
        cube = client.cubes.create(
            "Scorer",
            user_prompt="Rate {{text}}",
            output_type="structured",
            response_format=SCHEMA,
        )

    # One SDK argument, two wire fields.
    assert seen["body"]["is_structured"] is True
    assert seen["body"]["response_format"] == SCHEMA
    assert "output_kind" not in seen["body"]  # structured implies text
    assert cube.is_structured is True
    assert cube.response_format == SCHEMA
    assert cube.output_kind == "text"


def test_create_structured_can_state_the_schema_source():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = body_of(request)
        return httpx.Response(201, json=STRUCTURED_BODY)

    with make_client(handler) as client:
        client.cubes.create(
            "Scorer", user_prompt="Rate {{x}}", output_type="structured",
            response_format=SCHEMA, response_format_source="auto",
        )

    assert seen["body"]["response_format_source"] == "auto"


@pytest.mark.parametrize("kind", ["image", "audio"])
def test_create_binary_asserts_the_medium(kind):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = body_of(request)
        return httpx.Response(201, json={**CUBE_BODY, "output_kind": kind})

    with make_client(handler) as client:
        cube = client.cubes.create(
            f"{kind} maker",
            user_prompt="Make {{thing}}",
            models=[{"provider": "openai", "model_name": "gpt-5-image-mini", "rank": 0}],
            output_type=kind,
        )

    # Sent as output_kind — the server checks it against the stack.
    assert seen["body"]["output_kind"] == kind
    assert "is_structured" not in seen["body"]
    assert cube.output_kind == kind


def test_create_without_an_output_type_sends_nothing():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = body_of(request)
        return httpx.Response(201, json=CUBE_BODY)

    with make_client(handler) as client:
        cube = client.cubes.create("Plain", user_prompt="Hi {{x}}")

    assert "output_kind" not in seen["body"] and "is_structured" not in seen["body"]
    assert cube.output_kind == "text" and cube.is_structured is False


def test_schema_and_output_type_must_agree():
    """Caught locally, because the server's message would name ``is_structured``
    — a field this SDK doesn't have."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should never reach the network")

    with make_client(handler) as client:
        with pytest.raises(ValueError, match="requires response_format"):
            client.cubes.create("T", user_prompt="x", output_type="structured")

        with pytest.raises(ValueError, match='output_type="structured"'):
            client.cubes.create("T", user_prompt="x", response_format=SCHEMA)

        with pytest.raises(ValueError, match="Video is not available"):
            client.cubes.create("T", user_prompt="x", output_type="video")


def test_structured_cube_round_trips_through_retrieve():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=STRUCTURED_BODY)

    with make_client(handler) as client:
        cube = client.cubes.retrieve("cbe_plaincube0001")

    assert cube.is_structured is True and cube.response_format_source == "manual"


# ---- unsaved test overrides ---------------------------------------------------


def test_test_sends_unsaved_schema_and_declarations(request_log):
    from conftest import cube_success_body

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(200, json=cube_success_body('{"score": 9}'))

    with make_client(handler) as client:
        client.cubes.test(
            "cbe_plaincube0001",
            variables={"text": "hello"},
            response_format=SCHEMA,
            variable_definitions={"text": {"type": "string", "required": True}},
        )

    body = body_of(request_log[0])
    assert body["response_format"] == SCHEMA
    assert body["variable_definitions"] == {"text": {"type": "string", "required": True}}
    assert body["variables"] == {"text": "hello"}


def test_test_omits_the_overrides_when_not_given(request_log):
    from conftest import cube_success_body

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(200, json=cube_success_body())

    with make_client(handler) as client:
        client.cubes.test("cbe_plaincube0001", variables={"text": "hi"})

    body = body_of(request_log[0])
    assert "response_format" not in body and "variable_definitions" not in body


# ---- delete -------------------------------------------------------------------


def test_delete_sends_the_request(request_log):
    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(204)

    with make_client(handler) as client:
        assert client.cubes.delete("cbe_plaincube0001") is None

    assert request_log[0].method == "DELETE"
    assert request_log[0].url.path == "/v1/cubes/cbe_plaincube0001"


def test_delete_of_a_listed_cube_raises_conflict():
    def handler(request: httpx.Request) -> httpx.Response:
        return error_envelope(
            409, "This cube is publicly listed on the marketplace. Unlist it first.",
            "cube_listed",
        )

    with make_client(handler) as client:
        with pytest.raises(cubic.ConflictError) as e:
            client.cubes.delete("cbe_plaincube0001")

    assert e.value.error_code == "cube_listed"
