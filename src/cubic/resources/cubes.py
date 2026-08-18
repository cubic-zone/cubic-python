"""The cubes resource: read AND author cube definitions (owner-only).

The full authoring lifecycle is available by API key: ``create`` a cube, ``test``
unsaved wordings synchronously, ``update`` its config (which stages onto your
draft), ``create_version`` to publish (the server sizes a semantic bump to the
whole delta, content *and* config), ``versions`` for history, and ``set_channel``
to promote or roll back.

**A version is a promise about behaviour.** It snapshots the model stack, run
parameters, tools and callback URL alongside the content, so pinning version 12
runs version 12's models — not whatever the cube carries today. That is why
``update`` no longer applies config live: it writes to your draft, and
``create_version`` publishes it.

The sync and async classes are thin transport bindings over the shared payload
builders below.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal

from ..types import Channel, ChangelogEvent, Cube, CubeDraft, CubeVersion

if TYPE_CHECKING:
    import httpx

    from .._async_client import AsyncCubic
    from .._client import Cubic

RETRIEVE_DOC = """Fetch a cube's full definition — system instructions, user prompt
        template, variables schema, model stack, parameters, response format.

        ``version`` pins a historical version; omit it for the current one.

        Only your own cubes are readable: unknown IDs, other users' cubes, and
        marketplace cubes you subscribe to all raise
        :class:`~cubic.CubeNotFoundError` (definitions are the seller's IP).
        Polycube definitions are not yet available on this endpoint.
        """

CREATE_DOC = """Create a cube in one call — definition, model stack, and initial
        content (version 1 / "1.0.0") — and return it ready to run.

        ``models`` entries are ``{"provider": ..., "model_name": ..., "rank": n}``
        pairs exactly as listed by ``client.models.list()``; lower rank is tried
        first (fallback order). An entry may instead name a **model alias** —
        ``{"provider": "alias", "model_name": "fast-default", "rank": 0}`` —
        which resolves to whatever that alias points at when the run starts, so
        re-pointing it in the dashboard switches this cube without republishing
        it. Aliases are managed under Setup → Models and are not in the catalog.
        ``project_id`` is a public ``prj_…`` id from ``client.projects.list()``;
        omitted, the cube lands in the API key's created-in project (falling
        back to your default project).


        ``variables`` declares the cube's inputs — ``{name: {"type", "required",
        "description"}}``, most readably via :func:`cubic.variable`::

            variables={
                "contract": variable("file", description="The signed agreement"),
                "focus": variable(required=False),
            }

        Types are ``string`` (default), ``integer``, ``float``, ``boolean`` and
        ``file``. Any ``{{placeholder}}`` you don't declare is detected from the
        content and defaults to a required string, so declare only what differs.
        A ``file`` variable is what takes an uploaded file — see the Files
        section of the README; an ``att_`` id passed to any other variable is
        rejected rather than dereferenced.

        ``output_type`` picks what the cube produces, and is the one thing here
        that can never be changed afterwards — there is no converting a text cube
        into a structured one later, only ``delete`` and re-create.

        - ``"text"`` (the default) — plain text out.
        - ``"structured"`` — JSON conforming to ``response_format``, which is
          then REQUIRED. The schema is validated at save: an object root with
          named properties, types the platform can enforce, and a ``required``
          list naming properties that exist. Every model in the stack must
          support structured output.
        - ``"image"`` / ``"audio"`` — a **Binary Cube**. The medium actually
          comes from the models: naming an image stack is what makes an image
          cube, so passing ``output_type`` here is an assertion that the stack
          agrees (``output_kind_mismatch`` if it doesn't) rather than a setting.
          Every model in the stack must generate the same medium.

        Binary Cubes are narrower than text ones: no evals, no polycube
        membership, no ``contest`` completion type, no response format, and
        ``merge_responses`` does nothing. Video is not available — no model in
        the catalog generates it yet.

        Creation is idempotent: an ``Idempotency-Key`` header is attached
        (auto-generated unless ``idempotency_key`` is given), so transient
        failures retry safely without minting duplicate cubes.

        Raises :class:`~cubic.InvalidRequestError` for unknown models
        (``error_code="unknown_model"``), an alias you don't have
        (``error_code="alias_not_found"``), strategy violations, or invalid
        markers, and :class:`~cubic.NotFoundError` for a foreign/unknown
        ``project_id``.
        """

UPDATE_DOC = """Update a cube. Cosmetic fields apply now; behaviour is staged.

        ``title`` and ``description`` save immediately — they are cosmetic and
        cannot change what a run does. ``models``, ``parameters``,
        ``merge_responses`` and ``callback_url`` are written to your **draft**
        and take effect only when you ``create_version``; the returned cube
        reports ``draft_pending=True`` and still describes what is *published*.

        A cube's completion type and output kind are immutable after creation —
        changing either raises :class:`~cubic.CubicError`. Create a new cube.

        Only the fields you pass are changed; ``models`` replaces the whole
        stack when given.
        """

TEST_DOC = """Run the cube synchronously without saving anything — the
        prompt-iteration primitive.

        ``system_instructions`` / ``user_prompt`` are UNSAVED overrides: the run
        uses them (variables re-extracted from the overridden text) but no
        version is created and the stored definition is untouched. Loop
        test → judge → tweak at zero version cost, then commit the winning
        wording once with ``create_version``. Any callback URL is bypassed —
        the result always comes back in this call.

        ``response_format`` and ``variable_definitions`` are unsaved too, which
        is what lets a schema or a retyped variable be tried before it is
        committed — testing an edited declaration under the SAVED type would
        answer the wrong question. A response format may only be REPLACED, never
        introduced: a text cube can't be tested into a structured one.

        ``models``/``parameters`` behave exactly as on ``completions.create``.
        Returns a :class:`CompletionResult`; raises the same
        :class:`~cubic.CompletionError` subclasses as ``completions.create``.
        """

CREATE_VERSION_DOC = """Publish a version — an immutable snapshot of content AND config.

        Pass content to stage and publish in one call, or pass only
        ``change_note`` / ``bump_override`` to publish whatever is already on
        your draft (from ``update`` or ``update_draft``).

        A version is a COMPLETE snapshot, not a diff: when you pass content,
        pass both ``system_instructions`` and ``user_prompt`` every time (a
        field you omit is saved as empty, not carried over).

        The server sizes the semantic bump to the WHOLE delta and returns every
        rule that fired in ``bump_reason``. Major: a rank-0 model change, a
        response-format field removed or retyped, a newly required variable, or
        a large rewrite. Minor: a model added or removed, a schema field added,
        a tool toggled, a function marker changed. Patch: run parameters, the
        callback URL, or a reorder below rank 0. ``bump_override`` may RAISE the
        bump, never lower it.

        Publishing an identical draft raises :class:`~cubic.CubicError` with
        ``version_unchanged``.

        ``change_note`` is free text (max 500 chars) explaining why the version
        exists — the diff shows what changed, nothing else captures intent.

        ``variables`` declares the cube's inputs — ``{name: {"type", "required",
        "description"}}``, most readably via :func:`cubic.variable`::

            variables={
                "contract": variable("file", description="The signed agreement"),
                "focus": variable(required=False),
            }

        Types are ``string`` (default), ``integer``, ``float``, ``boolean`` and
        ``file``. Any ``{{placeholder}}`` you don't declare is detected from the
        content and defaults to a required string, so declare only what differs.
        A ``file`` variable is what takes an uploaded file — see the Files
        section of the README; an ``att_`` id passed to any other variable is
        rejected rather than dereferenced.
        """

VERSIONS_DOC = """List the cube's version history, newest first.

        Each entry carries the internal ``version_number`` (pin by it), the
        semantic ``version`` label, the ``author`` and ``change_note``, the
        ``bump_reason`` rules that sized the bump, and the ``channels`` pointing
        at it right now.
        """

SET_CURRENT_DOC = """Re-point which version completions serve (rollback / pin).

        Deprecated: this is an alias for moving the ``production`` channel. Use
        ``set_channel(cube_id, "production", n, reason=...)``, which records why
        on the changelog. History is immutable — only the pointer moves.
        """

DRAFT_DOC = """Your staged, unpublished changes.

        Nothing in a draft serves traffic. ``bump`` says what publishing would
        produce (``bump["level"]``, ``bump["next_label"]``) without publishing
        it, and ``changed_fields`` says which fields differ from the published
        version. ``has_draft`` is False when nothing is staged.
        """

UPDATE_DRAFT_DOC = """Stage a partial change without publishing it.

        ``content`` takes ``system_instructions``, ``user_prompt``, ``variables``,
        ``response_format``, ``response_format_source``. ``config`` takes
        ``models``, ``parameters``, ``merge_responses``, ``callback_url``.

        Useful for building a change up over several calls, or for checking what
        a bump would be before committing to it.
        """

DISCARD_DRAFT_DOC = """Throw away staged changes and go back to what is published."""

CHANNELS_DOC = """List the cube's channels — named, movable pointers to versions.

        ``production`` exists on every cube and is what a completion resolves to
        when it names neither a version nor a channel. ``latest`` is reserved
        (``reserved=True``), always resolves to the highest version, and cannot
        be moved or deleted.
        """

SET_CHANNEL_DOC = """Create or move a channel — the promote and rollback primitive.

        Takes effect immediately. Moving a channel to a LOWER version is a
        rollback: the changelog records it as one, naming the version pulled and
        how long it served. Pass a ``reason`` — it is the thing anyone reading
        the timeline afterwards actually wants.

        Publishing after a rollback will not silently move ``production`` back
        over the version you pulled.

        Channel names are 2–32 chars, lowercase ``[a-z0-9-]``, and normalized —
        ``"Staging"`` and ``"staging"`` are one channel. ``latest`` is reserved.
        """

DELETE_CHANNEL_DOC = """Delete a channel. ``production`` and ``latest`` refuse."""

CHANNEL_HISTORY_DOC = """Every move of one channel, newest first, with actor and reason."""

CHANGELOG_DOC = """This cube's timeline: publishes, channel moves and rollbacks.

        Filter with ``event_type`` (comma-separated), e.g.
        ``"version.published,version.rolled_back"``.
        """

OUTCOMES_DOC = """What each version actually did in production.

        Returns ``{"min_samples": n, "versions": {"14": {...}}}`` with requests,
        credits, cost per request, p50/p95 latency, success rate, wasted spend
        (cost on attempts that did not win) and eval pass rate.

        Deltas compare each version with ``baseline_version`` — the previous
        version that actually SERVED traffic, not the previous number — and are
        withheld entirely until both sides clear ``min_samples``. Check
        ``enough_data`` before reading ``deltas``: empty means "not enough data",
        not "no change".

        Playground and test runs are excluded throughout; they are stamped with a
        version but may have executed unsaved content.
        """

DIFF_DOC = """Diff any two versions — not only adjacent ones.

        Returns ``content`` (line diffs), ``config`` (a field table) and
        ``schema`` (added / removed / retyped / required-changed), plus the bump
        and the rules that produced it.
        """


OUTPUT_TYPES = ("text", "structured", "image", "audio")


def build_output_classification(
    output_type: str | None, response_format: dict[str, Any] | None
) -> dict[str, Any]:
    """Translate the SDK's one ``output_type`` into the wire's two fields.

    Checked here rather than server-side so the error names the argument the
    caller actually passed: the API talks about ``is_structured``, which is not
    a parameter of this SDK. The classification is immutable, so a mistake costs
    a delete and a re-create — worth one local check.
    """
    if output_type is not None and output_type not in OUTPUT_TYPES:
        raise ValueError(
            f"output_type must be one of {', '.join(OUTPUT_TYPES)} (got {output_type!r}). "
            "Video is not available — no catalog model generates it yet."
        )
    if output_type == "structured" and not response_format:
        raise ValueError(
            'output_type="structured" requires response_format — a structured cube '
            "always carries its schema, and it can't be added later."
        )
    if response_format and output_type != "structured":
        raise ValueError(
            'response_format needs output_type="structured". Only a structured cube '
            "carries one, and the classification is fixed at creation."
        )
    payload: dict[str, Any] = {}
    if output_type == "structured":
        payload["is_structured"] = True
    elif output_type is not None:
        # An assertion against the stack, not an override — see CREATE_DOC.
        payload["output_kind"] = output_type
    return payload


DELETE_DOC = """Delete a cube.

        The classification chosen at ``create`` — completion type, output type —
        can never be edited, so this is the only way to correct one: delete and
        re-create. A cube that has already run is retained behind the scenes so
        its completion history stays readable; one that never ran is removed.

        Raises :class:`~cubic.ConflictError` for a cube something else depends
        on: publicly listed on the marketplace (``cube_listed``), a node of a
        polycube (``cube_in_polycube``), or one of Cubic's own platform cubes
        (``system_cube_protected``). Unlist or delete the dependant first.
        """


def build_create_payload(
    title: str,
    *,
    system_instructions: str | None,
    user_prompt: str,
    models: list[dict[str, Any]] | None,
    completion_type: str | None,
    description: str | None,
    parameters: dict[str, Any] | None,
    callback_url: str | None,
    merge_responses: bool | None,
    variables: dict[str, dict[str, Any]] | None,
    project_id: str | None,
    output_type: str | None = None,
    response_format: dict[str, Any] | None = None,
    response_format_source: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"title": title, "user_prompt": user_prompt}
    payload.update(build_output_classification(output_type, response_format))
    if response_format is not None:
        payload["response_format"] = response_format
    if response_format_source is not None:
        payload["response_format_source"] = response_format_source
    if system_instructions is not None:
        payload["system_instructions"] = system_instructions
    if models is not None:
        payload["models"] = models
    if completion_type is not None:
        payload["completion_type"] = completion_type
    if description is not None:
        payload["description"] = description
    if parameters is not None:
        payload["parameters"] = parameters
    if callback_url is not None:
        payload["callback_url"] = callback_url
    if merge_responses is not None:
        payload["merge_responses"] = merge_responses
    if variables is not None:
        payload["variables"] = variables
    if project_id is not None:
        payload["project_id"] = project_id
    return payload


def build_update_payload(
    *,
    title: str | None,
    description: str | None,
    completion_type: str | None,
    callback_url: str | None,
    parameters: dict[str, Any] | None,
    merge_responses: bool | None,
    models: list[dict[str, Any]] | None,
    project_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if project_id is not None:
        payload["project_id"] = project_id
    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description
    if completion_type is not None:
        payload["completion_type"] = completion_type
    if callback_url is not None:
        payload["callback_url"] = callback_url
    if parameters is not None:
        payload["parameters"] = parameters
    if merge_responses is not None:
        payload["merge_responses"] = merge_responses
    if models is not None:
        payload["models"] = models
    return payload


def build_version_payload(
    *,
    system_instructions: str | None,
    user_prompt: str | None,
    variables: dict[str, dict[str, Any]] | None,
    response_format: dict[str, Any] | None,
    response_format_source: str | None,
    change_note: str | None = None,
    bump_override: str | None = None,
) -> dict[str, Any]:
    # An omitted user_prompt means "publish what is staged" rather than
    # "publish an empty prompt" — sending "" would wipe the draft's text.
    payload: dict[str, Any] = {}
    if user_prompt is not None:
        payload["user_prompt"] = user_prompt
    if change_note is not None:
        payload["change_note"] = change_note
    if bump_override is not None:
        payload["bump_override"] = bump_override
    if system_instructions is not None:
        payload["system_instructions"] = system_instructions
    if variables is not None:
        payload["variables"] = variables
    if response_format is not None:
        payload["response_format"] = response_format
    if response_format_source is not None:
        payload["response_format_source"] = response_format_source
    return payload


def build_draft_payload(
    *,
    content: dict[str, Any] | None,
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if content is not None:
        payload["content"] = content
    if config is not None:
        payload["config"] = config
    return payload


def parse_channels(response: "httpx.Response") -> list[Channel]:
    return [Channel.model_validate(c) for c in response.json()]


def parse_changelog(response: "httpx.Response") -> list[ChangelogEvent]:
    return [ChangelogEvent.model_validate(e) for e in response.json()]


def build_test_payload(
    *,
    variables: dict[str, Any] | list[dict[str, Any]] | None,
    system_instructions: str | None,
    user_prompt: str | None,
    version: int | None,
    history: list[dict[str, str]] | None,
    models: list[dict[str, Any]] | None,
    parameters: dict[str, Any] | None,
    use_response_cache: bool,
    test_mode: bool,
    test_response_content: str | dict[str, str] | None,
    client_request_id: str | uuid.UUID | None,
    variable_definitions: dict[str, dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if variables is not None:
        payload["variables"] = variables
    if variable_definitions is not None:
        payload["variable_definitions"] = variable_definitions
    if response_format is not None:
        payload["response_format"] = response_format
    if system_instructions is not None:
        payload["system_instructions"] = system_instructions
    if user_prompt is not None:
        payload["user_prompt"] = user_prompt
    if version is not None:
        payload["version_number"] = version
    if history is not None:
        payload["history"] = history
    if models is not None:
        payload["models"] = models
    if parameters is not None:
        payload["parameters"] = parameters
    if not use_response_cache:
        payload["use_response_cache"] = False
    if test_mode:
        payload["test_mode"] = True
    if test_response_content is not None:
        payload["test_response_content"] = test_response_content
    if client_request_id is not None:
        payload["client_request_id"] = str(client_request_id)
    return payload


def parse_test_result(response: "httpx.Response"):
    """A test run is always a plain-cube CompletionResult; reuse the completion
    pipeline's status→exception mapping."""
    from ..types import CompletionResult
    from .completions import pipeline_error

    result = CompletionResult.model_validate(response.json())
    if result.status == "error":
        raise pipeline_error(result, response.headers.get("X-Request-ID"))
    return result


def build_retrieve_params(
    version: int | None, channel: str | None
) -> dict[str, Any] | None:
    """Query params for a cube read. Both set is refused here rather than
    server-side, so the mistake surfaces without a round trip — a channel moves
    and a version does not, so asking for both has no coherent answer."""
    if version is not None and channel is not None:
        raise TypeError("Pass either version or channel, not both")
    if version is not None:
        return {"version_number": version}
    if channel is not None:
        return {"channel": channel}
    return None


def parse_versions(response: "httpx.Response") -> list[CubeVersion]:
    return [CubeVersion.model_validate(v) for v in response.json()]


class Cubes:
    def __init__(self, client: "Cubic") -> None:
        self._client = client

    def retrieve(
        self, cube_id: str, *, version: int | None = None, channel: str | None = None
    ) -> Cube:
        """The cube's definition as a version or channel serves it.

        Neither argument reads what ``production`` serves. Prompts come back as
        written — variables and function markers unsubstituted — and no
        completion is recorded. ``cube_id`` also accepts ``cbe_…@staging``.
        """
        params = build_retrieve_params(version, channel)
        response = self._client.request(
            "GET", f"/v1/cubes/{cube_id}", params=params, idempotent=True
        )
        return Cube.model_validate(response.json())

    def create(
        self,
        title: str,
        *,
        system_instructions: str | None = None,
        user_prompt: str = "",
        models: list[dict[str, Any]] | None = None,
        completion_type: str | None = None,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
        callback_url: str | None = None,
        merge_responses: bool | None = None,
        variables: dict[str, dict[str, Any]] | None = None,
        project_id: str | None = None,
        output_type: Literal["text", "structured", "image", "audio"] | None = None,
        response_format: dict[str, Any] | None = None,
        response_format_source: Literal["manual", "auto"] | None = None,
        idempotency_key: str | None = None,
    ) -> Cube:
        payload = build_create_payload(
            title,
            system_instructions=system_instructions,
            user_prompt=user_prompt,
            models=models,
            completion_type=completion_type,
            description=description,
            parameters=parameters,
            callback_url=callback_url,
            merge_responses=merge_responses,
            variables=variables,
            project_id=project_id,
            output_type=output_type,
            response_format=response_format,
            response_format_source=response_format_source,
        )
        response = self._client.request(
            "POST",
            "/v1/cubes",
            json_body=payload,
            idempotent=True,  # safe: the Idempotency-Key makes retries replay, not duplicate
            extra_headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
        )
        return Cube.model_validate(response.json())

    def update(
        self,
        cube_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        completion_type: str | None = None,
        callback_url: str | None = None,
        parameters: dict[str, Any] | None = None,
        merge_responses: bool | None = None,
        models: list[dict[str, Any]] | None = None,
        project_id: str | None = None,
    ) -> Cube:
        payload = build_update_payload(
            title=title,
            description=description,
            completion_type=completion_type,
            callback_url=callback_url,
            parameters=parameters,
            merge_responses=merge_responses,
            models=models,
            project_id=project_id,
        )
        response = self._client.request("PATCH", f"/v1/cubes/{cube_id}", json_body=payload)
        return Cube.model_validate(response.json())

    def delete(self, cube_id: str) -> None:
        self._client.request("DELETE", f"/v1/cubes/{cube_id}", idempotent=True)

    def test(
        self,
        cube_id: str,
        variables: dict[str, Any] | list[dict[str, Any]] | None = None,
        *,
        system_instructions: str | None = None,
        user_prompt: str | None = None,
        variable_definitions: dict[str, dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        version: int | None = None,
        history: list[dict[str, str]] | None = None,
        models: list[dict[str, Any]] | None = None,
        parameters: dict[str, Any] | None = None,
        use_response_cache: bool = True,
        test_mode: bool = False,
        test_response_content: str | dict[str, str] | None = None,
        client_request_id: str | uuid.UUID | None = None,
    ):
        payload = build_test_payload(
            variables=variables,
            system_instructions=system_instructions,
            user_prompt=user_prompt,
            variable_definitions=variable_definitions,
            response_format=response_format,
            version=version,
            history=history,
            models=models,
            parameters=parameters,
            use_response_cache=use_response_cache,
            test_mode=test_mode,
            test_response_content=test_response_content,
            client_request_id=client_request_id,
        )
        response = self._client.request("POST", f"/v1/cubes/{cube_id}/test", json_body=payload)
        return parse_test_result(response)

    def create_version(
        self,
        cube_id: str,
        *,
        user_prompt: str | None = None,
        system_instructions: str | None = None,
        variables: dict[str, dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        response_format_source: str | None = None,
        change_note: str | None = None,
        bump_override: str | None = None,
    ) -> CubeVersion:
        payload = build_version_payload(
            system_instructions=system_instructions,
            user_prompt=user_prompt,
            variables=variables,
            response_format=response_format,
            response_format_source=response_format_source,
            change_note=change_note,
            bump_override=bump_override,
        )
        response = self._client.request(
            "POST", f"/v1/cubes/{cube_id}/versions", json_body=payload
        )
        return CubeVersion.model_validate(response.json())

    def versions(self, cube_id: str) -> list[CubeVersion]:
        response = self._client.request(
            "GET", f"/v1/cubes/{cube_id}/versions", idempotent=True
        )
        return parse_versions(response)

    def set_current_version(self, cube_id: str, version_number: int) -> Cube:
        response = self._client.request(
            "PUT",
            f"/v1/cubes/{cube_id}/current-version",
            json_body={"version_number": version_number},
            idempotent=True,  # PUT of a pointer — replaying it is harmless
        )
        return Cube.model_validate(response.json())

    # ---- drafts ----------------------------------------------------------
    def draft(self, cube_id: str) -> CubeDraft:
        response = self._client.request(
            "GET", f"/v1/cubes/{cube_id}/draft", idempotent=True
        )
        return CubeDraft.model_validate(response.json())

    def update_draft(
        self,
        cube_id: str,
        *,
        content: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> CubeDraft:
        response = self._client.request(
            "PATCH",
            f"/v1/cubes/{cube_id}/draft",
            json_body=build_draft_payload(content=content, config=config),
        )
        return CubeDraft.model_validate(response.json())

    def discard_draft(self, cube_id: str) -> None:
        self._client.request("DELETE", f"/v1/cubes/{cube_id}/draft", idempotent=True)

    # ---- channels --------------------------------------------------------
    def channels(self, cube_id: str) -> list[Channel]:
        response = self._client.request(
            "GET", f"/v1/cubes/{cube_id}/channels", idempotent=True
        )
        return parse_channels(response)

    def set_channel(
        self, cube_id: str, name: str, version_number: int, *, reason: str | None = None
    ) -> Channel:
        response = self._client.request(
            "PUT",
            f"/v1/cubes/{cube_id}/channels/{name}",
            json_body={"version_number": version_number, "reason": reason},
            idempotent=True,  # PUT of a pointer — replaying it is harmless
        )
        return Channel.model_validate(response.json())

    def delete_channel(self, cube_id: str, name: str) -> None:
        self._client.request(
            "DELETE", f"/v1/cubes/{cube_id}/channels/{name}", idempotent=True
        )

    def channel_history(self, cube_id: str, name: str) -> list[dict[str, Any]]:
        response = self._client.request(
            "GET", f"/v1/cubes/{cube_id}/channels/{name}/history", idempotent=True
        )
        return response.json()

    # ---- changelog + diff -------------------------------------------------
    def changelog(
        self, cube_id: str, *, event_type: str | None = None, limit: int = 50
    ) -> list[ChangelogEvent]:
        query = f"?limit={limit}" + (f"&event_type={event_type}" if event_type else "")
        response = self._client.request(
            "GET", f"/v1/cubes/{cube_id}/changelog{query}", idempotent=True
        )
        return parse_changelog(response)

    def diff(self, cube_id: str, a: int, b: int) -> dict[str, Any]:
        response = self._client.request(
            "GET", f"/v1/cubes/{cube_id}/versions/{a}/diff/{b}", idempotent=True
        )
        return response.json()

    def outcomes(self, cube_id: str) -> dict[str, Any]:
        response = self._client.request(
            "GET", f"/v1/cubes/{cube_id}/outcomes", idempotent=True
        )
        return response.json()

    retrieve.__doc__ = RETRIEVE_DOC
    create.__doc__ = CREATE_DOC
    update.__doc__ = UPDATE_DOC
    delete.__doc__ = DELETE_DOC
    test.__doc__ = TEST_DOC
    create_version.__doc__ = CREATE_VERSION_DOC
    versions.__doc__ = VERSIONS_DOC
    set_current_version.__doc__ = SET_CURRENT_DOC
    draft.__doc__ = DRAFT_DOC
    update_draft.__doc__ = UPDATE_DRAFT_DOC
    discard_draft.__doc__ = DISCARD_DRAFT_DOC
    channels.__doc__ = CHANNELS_DOC
    set_channel.__doc__ = SET_CHANNEL_DOC
    delete_channel.__doc__ = DELETE_CHANNEL_DOC
    channel_history.__doc__ = CHANNEL_HISTORY_DOC
    changelog.__doc__ = CHANGELOG_DOC
    diff.__doc__ = DIFF_DOC
    outcomes.__doc__ = OUTCOMES_DOC


class AsyncCubes:
    def __init__(self, client: "AsyncCubic") -> None:
        self._client = client

    async def retrieve(
        self, cube_id: str, *, version: int | None = None, channel: str | None = None
    ) -> Cube:
        """The cube's definition as a version or channel serves it.

        Neither argument reads what ``production`` serves. Prompts come back as
        written — variables and function markers unsubstituted — and no
        completion is recorded. ``cube_id`` also accepts ``cbe_…@staging``.
        """
        params = build_retrieve_params(version, channel)
        response = await self._client.request(
            "GET", f"/v1/cubes/{cube_id}", params=params, idempotent=True
        )
        return Cube.model_validate(response.json())

    async def create(
        self,
        title: str,
        *,
        system_instructions: str | None = None,
        user_prompt: str = "",
        models: list[dict[str, Any]] | None = None,
        completion_type: str | None = None,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
        callback_url: str | None = None,
        merge_responses: bool | None = None,
        variables: dict[str, dict[str, Any]] | None = None,
        project_id: str | None = None,
        output_type: Literal["text", "structured", "image", "audio"] | None = None,
        response_format: dict[str, Any] | None = None,
        response_format_source: Literal["manual", "auto"] | None = None,
        idempotency_key: str | None = None,
    ) -> Cube:
        payload = build_create_payload(
            title,
            system_instructions=system_instructions,
            user_prompt=user_prompt,
            models=models,
            completion_type=completion_type,
            description=description,
            parameters=parameters,
            callback_url=callback_url,
            merge_responses=merge_responses,
            variables=variables,
            project_id=project_id,
            output_type=output_type,
            response_format=response_format,
            response_format_source=response_format_source,
        )
        response = await self._client.request(
            "POST",
            "/v1/cubes",
            json_body=payload,
            idempotent=True,
            extra_headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
        )
        return Cube.model_validate(response.json())

    async def update(
        self,
        cube_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        completion_type: str | None = None,
        callback_url: str | None = None,
        parameters: dict[str, Any] | None = None,
        merge_responses: bool | None = None,
        models: list[dict[str, Any]] | None = None,
        project_id: str | None = None,
    ) -> Cube:
        payload = build_update_payload(
            title=title,
            description=description,
            completion_type=completion_type,
            callback_url=callback_url,
            parameters=parameters,
            merge_responses=merge_responses,
            models=models,
            project_id=project_id,
        )
        response = await self._client.request("PATCH", f"/v1/cubes/{cube_id}", json_body=payload)
        return Cube.model_validate(response.json())

    async def delete(self, cube_id: str) -> None:
        await self._client.request("DELETE", f"/v1/cubes/{cube_id}", idempotent=True)

    async def test(
        self,
        cube_id: str,
        variables: dict[str, Any] | list[dict[str, Any]] | None = None,
        *,
        system_instructions: str | None = None,
        user_prompt: str | None = None,
        variable_definitions: dict[str, dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        version: int | None = None,
        history: list[dict[str, str]] | None = None,
        models: list[dict[str, Any]] | None = None,
        parameters: dict[str, Any] | None = None,
        use_response_cache: bool = True,
        test_mode: bool = False,
        test_response_content: str | dict[str, str] | None = None,
        client_request_id: str | uuid.UUID | None = None,
    ):
        payload = build_test_payload(
            variables=variables,
            system_instructions=system_instructions,
            user_prompt=user_prompt,
            variable_definitions=variable_definitions,
            response_format=response_format,
            version=version,
            history=history,
            models=models,
            parameters=parameters,
            use_response_cache=use_response_cache,
            test_mode=test_mode,
            test_response_content=test_response_content,
            client_request_id=client_request_id,
        )
        response = await self._client.request(
            "POST", f"/v1/cubes/{cube_id}/test", json_body=payload
        )
        return parse_test_result(response)

    async def create_version(
        self,
        cube_id: str,
        *,
        user_prompt: str | None = None,
        system_instructions: str | None = None,
        variables: dict[str, dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        response_format_source: str | None = None,
        change_note: str | None = None,
        bump_override: str | None = None,
    ) -> CubeVersion:
        payload = build_version_payload(
            system_instructions=system_instructions,
            user_prompt=user_prompt,
            variables=variables,
            response_format=response_format,
            response_format_source=response_format_source,
            change_note=change_note,
            bump_override=bump_override,
        )
        response = await self._client.request(
            "POST", f"/v1/cubes/{cube_id}/versions", json_body=payload
        )
        return CubeVersion.model_validate(response.json())

    async def versions(self, cube_id: str) -> list[CubeVersion]:
        response = await self._client.request(
            "GET", f"/v1/cubes/{cube_id}/versions", idempotent=True
        )
        return parse_versions(response)

    # ---- drafts ----------------------------------------------------------
    async def draft(self, cube_id: str) -> CubeDraft:
        response = await self._client.request(
            "GET", f"/v1/cubes/{cube_id}/draft", idempotent=True
        )
        return CubeDraft.model_validate(response.json())

    async def update_draft(
        self,
        cube_id: str,
        *,
        content: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> CubeDraft:
        response = await self._client.request(
            "PATCH",
            f"/v1/cubes/{cube_id}/draft",
            json_body=build_draft_payload(content=content, config=config),
        )
        return CubeDraft.model_validate(response.json())

    async def discard_draft(self, cube_id: str) -> None:
        await self._client.request("DELETE", f"/v1/cubes/{cube_id}/draft", idempotent=True)

    # ---- channels --------------------------------------------------------
    async def channels(self, cube_id: str) -> list[Channel]:
        response = await self._client.request(
            "GET", f"/v1/cubes/{cube_id}/channels", idempotent=True
        )
        return parse_channels(response)

    async def set_channel(
        self, cube_id: str, name: str, version_number: int, *, reason: str | None = None
    ) -> Channel:
        response = await self._client.request(
            "PUT",
            f"/v1/cubes/{cube_id}/channels/{name}",
            json_body={"version_number": version_number, "reason": reason},
            idempotent=True,
        )
        return Channel.model_validate(response.json())

    async def delete_channel(self, cube_id: str, name: str) -> None:
        await self._client.request(
            "DELETE", f"/v1/cubes/{cube_id}/channels/{name}", idempotent=True
        )

    async def channel_history(self, cube_id: str, name: str) -> list[dict[str, Any]]:
        response = await self._client.request(
            "GET", f"/v1/cubes/{cube_id}/channels/{name}/history", idempotent=True
        )
        return response.json()

    # ---- changelog + diff -------------------------------------------------
    async def changelog(
        self, cube_id: str, *, event_type: str | None = None, limit: int = 50
    ) -> list[ChangelogEvent]:
        query = f"?limit={limit}" + (f"&event_type={event_type}" if event_type else "")
        response = await self._client.request(
            "GET", f"/v1/cubes/{cube_id}/changelog{query}", idempotent=True
        )
        return parse_changelog(response)

    async def diff(self, cube_id: str, a: int, b: int) -> dict[str, Any]:
        response = await self._client.request(
            "GET", f"/v1/cubes/{cube_id}/versions/{a}/diff/{b}", idempotent=True
        )
        return response.json()

    async def outcomes(self, cube_id: str) -> dict[str, Any]:
        response = await self._client.request(
            "GET", f"/v1/cubes/{cube_id}/outcomes", idempotent=True
        )
        return response.json()

    async def set_current_version(self, cube_id: str, version_number: int) -> Cube:
        response = await self._client.request(
            "PUT",
            f"/v1/cubes/{cube_id}/current-version",
            json_body={"version_number": version_number},
            idempotent=True,
        )
        return Cube.model_validate(response.json())

    retrieve.__doc__ = RETRIEVE_DOC
    create.__doc__ = CREATE_DOC
    update.__doc__ = UPDATE_DOC
    delete.__doc__ = DELETE_DOC
    test.__doc__ = TEST_DOC
    create_version.__doc__ = CREATE_VERSION_DOC
    versions.__doc__ = VERSIONS_DOC
    set_current_version.__doc__ = SET_CURRENT_DOC
    draft.__doc__ = DRAFT_DOC
    update_draft.__doc__ = UPDATE_DRAFT_DOC
    discard_draft.__doc__ = DISCARD_DRAFT_DOC
    channels.__doc__ = CHANNELS_DOC
    set_channel.__doc__ = SET_CHANNEL_DOC
    delete_channel.__doc__ = DELETE_CHANNEL_DOC
    channel_history.__doc__ = CHANNEL_HISTORY_DOC
    changelog.__doc__ = CHANGELOG_DOC
    diff.__doc__ = DIFF_DOC
    outcomes.__doc__ = OUTCOMES_DOC
