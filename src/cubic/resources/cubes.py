"""The cubes resource: read AND author cube definitions (owner-only).

The full authoring lifecycle is available by API key: ``create`` a cube,
``update`` its prompt-level config, ``test`` unsaved wordings synchronously,
``create_version`` to publish content (the server sizes a semantic version bump
to the delta), ``versions`` for history, and ``set_current_version`` to roll
back. The sync and async classes are thin transport bindings over the shared
payload builders below.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from ..types import Cube, CubeVersion

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
        first (fallback order). ``project_id`` is a public ``prj_…`` id from
        ``client.projects.list()``; omitted, the cube lands in the API key's
        created-in project (falling back to your default project).

        Creation is idempotent: an ``Idempotency-Key`` header is attached
        (auto-generated unless ``idempotency_key`` is given), so transient
        failures retry safely without minting duplicate cubes.

        Raises :class:`~cubic.InvalidRequestError` for unknown models
        (``error_code="unknown_model"``), strategy violations, or invalid
        markers, and :class:`~cubic.NotFoundError` for a foreign/unknown
        ``project_id``.
        """

UPDATE_DOC = """Update prompt-level fields: title, description, model stack,
        parameters, callback URL, completion strategy, merge behavior.

        These are never versioned. To change the cube's *content* — system
        instructions, user prompt, response format — use ``create_version``.
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

        ``models``/``parameters`` behave exactly as on ``completions.create``.
        Returns a :class:`CompletionResult`; raises the same
        :class:`~cubic.CompletionError` subclasses as ``completions.create``.
        """

CREATE_VERSION_DOC = """Publish new content as a new version and make it current.

        A version is a COMPLETE snapshot, not a diff: pass both
        ``system_instructions`` and ``user_prompt`` every time (a field you
        omit is saved as empty, not carried over).

        The server sizes the semantic bump to the content delta and returns it
        on the :class:`CubeVersion`: a tiny tweak bumps patch, a moderate edit
        minor, a large rewrite major; structural response-format changes floor
        the bump at minor. ``change_ratio`` (0 identical … 1 fully different)
        is useful feedback when iterating programmatically.
        """

VERSIONS_DOC = """List the cube's version history, newest first.

        Each entry carries the internal ``version_number`` (pin/rollback by
        it), the semantic ``version`` label, its ``change_ratio``, and whether
        completions currently serve it (``is_current``).
        """

SET_CURRENT_DOC = """Re-point which version completions serve (rollback / pin).

        History is immutable — only the pointer moves; no new version is
        created. Returns the cube as now served. Raises
        :class:`~cubic.VersionNotFoundError` for an unknown ``version_number``.
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
) -> dict[str, Any]:
    payload: dict[str, Any] = {"title": title, "user_prompt": user_prompt}
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
    user_prompt: str,
    variables: dict[str, dict[str, Any]] | None,
    response_format: dict[str, Any] | None,
    response_format_source: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"user_prompt": user_prompt}
    if system_instructions is not None:
        payload["system_instructions"] = system_instructions
    if variables is not None:
        payload["variables"] = variables
    if response_format is not None:
        payload["response_format"] = response_format
    if response_format_source is not None:
        payload["response_format_source"] = response_format_source
    return payload


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
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if variables is not None:
        payload["variables"] = variables
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


def parse_versions(response: "httpx.Response") -> list[CubeVersion]:
    return [CubeVersion.model_validate(v) for v in response.json()]


class Cubes:
    def __init__(self, client: "Cubic") -> None:
        self._client = client

    def retrieve(self, cube_id: str, *, version: int | None = None) -> Cube:
        params = {"version_number": version} if version is not None else None
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

    def test(
        self,
        cube_id: str,
        variables: dict[str, Any] | list[dict[str, Any]] | None = None,
        *,
        system_instructions: str | None = None,
        user_prompt: str | None = None,
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
        user_prompt: str,
        system_instructions: str | None = None,
        variables: dict[str, dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        response_format_source: str | None = None,
    ) -> CubeVersion:
        payload = build_version_payload(
            system_instructions=system_instructions,
            user_prompt=user_prompt,
            variables=variables,
            response_format=response_format,
            response_format_source=response_format_source,
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

    retrieve.__doc__ = RETRIEVE_DOC
    create.__doc__ = CREATE_DOC
    update.__doc__ = UPDATE_DOC
    test.__doc__ = TEST_DOC
    create_version.__doc__ = CREATE_VERSION_DOC
    versions.__doc__ = VERSIONS_DOC
    set_current_version.__doc__ = SET_CURRENT_DOC


class AsyncCubes:
    def __init__(self, client: "AsyncCubic") -> None:
        self._client = client

    async def retrieve(self, cube_id: str, *, version: int | None = None) -> Cube:
        params = {"version_number": version} if version is not None else None
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

    async def test(
        self,
        cube_id: str,
        variables: dict[str, Any] | list[dict[str, Any]] | None = None,
        *,
        system_instructions: str | None = None,
        user_prompt: str | None = None,
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
        user_prompt: str,
        system_instructions: str | None = None,
        variables: dict[str, dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        response_format_source: str | None = None,
    ) -> CubeVersion:
        payload = build_version_payload(
            system_instructions=system_instructions,
            user_prompt=user_prompt,
            variables=variables,
            response_format=response_format,
            response_format_source=response_format_source,
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
    test.__doc__ = TEST_DOC
    create_version.__doc__ = CREATE_VERSION_DOC
    versions.__doc__ = VERSIONS_DOC
    set_current_version.__doc__ = SET_CURRENT_DOC
