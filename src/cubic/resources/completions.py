"""The completions resource: run a cube (or polycube), fetch and wait on runs.

The sync and async clients share the payload-building, response-parsing, and
error-mapping logic below; the resource classes are thin transport bindings.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import TYPE_CHECKING, Any, Callable

from .. import _exceptions as err
from ..types import AttemptError, CompletionRecord, CompletionResult, PolycubeResult
from .attachments import AttachmentInput, build_attachment_entries

if TYPE_CHECKING:
    import httpx

    from .._async_client import AsyncCubic
    from .._client import Cubic

_VARIABLE_RE = re.compile(r"[Vv]ariable '([^']+)'")

_WAIT_MAX_DELAY = 4.0

CREATE_DOC = """Run a cube or polycube by its public ID.

        The ID may refer to either kind — the API dispatches on it and this
        method returns a :class:`CompletionResult` or :class:`PolycubeResult`
        accordingly; ``result.content`` is the delivered output for both.

        ``version``, ``history``, ``models``, ``parameters``, batch
        ``variables``, and ``client_request_id`` apply to plain cubes only;
        the API rejects them for polycubes.

        ``attachments`` works for both kinds (a polycube delivers them to its
        first cube). Each entry is an ``att_…`` id (from
        ``client.attachments.upload``), an :class:`~cubic.types.Attachment`,
        a :class:`~pathlib.Path`, or ``(filename, bytes)`` — the latter two
        are sent inline. Every model in the cube's stack must accept
        native-tier files (PDF/images) or the API rejects the run with 422
        ``attachment_not_supported``.

        A ``client_request_id`` is attached automatically for plain cubes so
        transient failures can be retried without double-executing. When
        ``callback_url`` is set (here or on the cube), the run is queued
        (``result.is_queued``) and the result is delivered to the callback —
        call ``result.wait()`` if you also need it in-process.

        Raises a :class:`~cubic.CompletionError` subclass when the pipeline
        fails, and transport-level :class:`~cubic.CubicError` subclasses for
        auth/validation/quota/HTTP errors.
        """


def build_create_payload(
    cube_id: str,
    variables: dict[str, Any] | list[dict[str, Any]] | None,
    *,
    version: int | None,
    history: list[dict[str, str]] | None,
    models: list[dict[str, Any]] | None,
    parameters: dict[str, Any] | None,
    callback_url: str | None,
    use_response_cache: bool,
    test_mode: bool,
    test_response_content: str | dict[str, str] | None,
    metadata: dict[str, Any] | None,
    client_request_id: str | uuid.UUID | None,
    known_polycube: bool,
    attachments: list[AttachmentInput] | None = None,
) -> tuple[dict[str, Any], bool, str | None]:
    """Build the POST /v1/completions body.

    Returns ``(payload, caller_supplied_cube_only, auto_request_id)`` —
    the latter two drive the polycube-fallback retry decision.
    """
    auto_request_id: str | None = None
    if client_request_id is None and not known_polycube:
        auto_request_id = str(uuid.uuid4())

    payload: dict[str, Any] = {"prompt_id": cube_id}
    if variables is not None:
        payload["variables"] = variables
    if version is not None:
        payload["version_number"] = version
    if history is not None:
        payload["history"] = history
    if models is not None:
        payload["models"] = models
    if parameters is not None:
        payload["parameters"] = parameters
    if callback_url is not None:
        payload["callback_url"] = callback_url
    if not use_response_cache:
        payload["use_response_cache"] = False
    if test_mode:
        payload["test_mode"] = True
    if test_response_content is not None:
        payload["test_response_content"] = test_response_content
    if metadata is not None:
        payload["metadata"] = metadata
    if attachments:
        # Valid for both kinds (root-node delivery on polycubes), so it plays
        # no part in the polycube-fallback retry decision below.
        payload["attachments"] = build_attachment_entries(attachments)
    if client_request_id is not None:
        payload["client_request_id"] = str(client_request_id)
    elif auto_request_id is not None:
        payload["client_request_id"] = auto_request_id

    # Only the SDK-injected idempotency key can be conflicting when none of
    # the cube-only inputs were supplied by the caller.
    caller_supplied_cube_only = (
        version is not None
        or history is not None
        or models is not None
        or parameters is not None
        or client_request_id is not None
        or isinstance(variables, list)
    )
    return payload, caller_supplied_cube_only, auto_request_id


def should_retry_as_polycube(
    e: err.InvalidRequestError, auto_request_id: str | None, caller_supplied_cube_only: bool
) -> bool:
    """True when a 422 was caused solely by our auto idempotency key hitting a
    polycube — the request should be resent without it."""
    return (
        e.error_code == "polycube_field_not_applicable"
        and auto_request_id is not None
        and not caller_supplied_cube_only
    )


def parse_result(
    response: "httpx.Response",
    cube_id: str,
    kind_cache: dict[str, str],
    waiter: Callable[..., Any] | None,
) -> CompletionResult | PolycubeResult:
    data = response.json()
    result: CompletionResult | PolycubeResult
    if isinstance(data, dict) and "chain_id" in data:
        result = PolycubeResult.model_validate(data)
        kind_cache[cube_id] = "polycube"
    else:
        result = CompletionResult.model_validate(data)
        kind_cache.setdefault(cube_id, "cube")
    if result.status == "error":
        raise pipeline_error(result, response.headers.get("X-Request-ID"))
    result._waiter = waiter
    return result


def pipeline_error(
    result: CompletionResult | PolycubeResult, request_id: str | None
) -> err.CubicError:
    """Turn a 200-with-status:"error" response into a typed exception."""
    first: AttemptError | None = result.attempt_errors[0] if result.attempt_errors else None
    prefix = ""
    if first is None and isinstance(result, PolycubeResult):
        for segment in result.segments:
            if segment.error is not None:
                first = segment.error
                prefix = f"node '{segment.node_key}' ({segment.cube_id}): "
                break

    common: dict[str, Any] = {
        "request_id": request_id,
        "result": result,
        "attempt_errors": result.attempt_errors,
    }
    if first is None:
        return err.CompletionError("Completion failed (no error detail returned)", **common)

    message = prefix + first.message
    common["error_code"] = first.error_code

    if first.stage == "prompt_resolution":
        # The cube (or a node's cube version) could not be resolved.
        return err.CubeNotFoundError(
            message,
            error_code=first.error_code,
            request_id=request_id,
        )
    if first.stage == "variable_validation" or first.error_code == "content_error":
        match = _VARIABLE_RE.search(first.message)
        return err.MissingVariableError(
            message, variable_name=match.group(1) if match else None, **common
        )
    if first.stage in ("llm_call", "credential_decrypt"):
        return err.ProviderError(message, **common)
    return err.CompletionError(message, **common)


def record_error(record: CompletionRecord) -> err.CompletionError:
    """Build the exception for a persisted record whose status is "error"."""
    detail = record.error_detail
    first: dict[str, Any] = {}
    if isinstance(detail, dict):
        first = detail
    elif isinstance(detail, list) and detail and isinstance(detail[0], dict):
        first = detail[0]
    message = first.get("message") or f"Completion {record.request_id} finished with status 'error'"
    return err.CompletionError(
        message,
        error_code=first.get("error_code"),
        request_id=record.request_id,
        result=record,
    )


def _next_poll_delay(current: float, fixed: float | None) -> float:
    return fixed if fixed is not None else min(current * 2, _WAIT_MAX_DELAY)


WAIT_DOC = """Poll until the completion's persisted result is available.

        Returns the :class:`CompletionRecord` once the run has been executed
        and persisted, raising :class:`~cubic.CompletionError` if it finished
        with status ``error`` and :class:`~cubic.WaitTimeoutError` if nothing
        was persisted within ``timeout`` seconds (the run itself is unaffected
        — the ``request_id`` remains valid).

        ``poll_interval`` fixes the delay between polls; by default the delay
        backs off from 0.5s to 4s. Works for any run — queued (callback) runs
        are the main use, but a sync run's record also becomes retrievable
        once the persistence worker has processed it.
        """


class Completions:
    def __init__(self, client: "Cubic") -> None:
        self._client = client

    def create(
        self,
        cube_id: str,
        variables: dict[str, Any] | list[dict[str, Any]] | None = None,
        *,
        version: int | None = None,
        history: list[dict[str, str]] | None = None,
        models: list[dict[str, Any]] | None = None,
        parameters: dict[str, Any] | None = None,
        callback_url: str | None = None,
        use_response_cache: bool = True,
        test_mode: bool = False,
        test_response_content: str | dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
        attachments: list[AttachmentInput] | None = None,
        client_request_id: str | uuid.UUID | None = None,
    ) -> CompletionResult | PolycubeResult:
        payload, caller_supplied_cube_only, auto_request_id = build_create_payload(
            cube_id,
            variables,
            version=version,
            history=history,
            models=models,
            parameters=parameters,
            callback_url=callback_url,
            use_response_cache=use_response_cache,
            test_mode=test_mode,
            test_response_content=test_response_content,
            metadata=metadata,
            client_request_id=client_request_id,
            known_polycube=self._client._kind_cache.get(cube_id) == "polycube",
            attachments=attachments,
        )
        try:
            response = self._client.request(
                "POST",
                "/v1/completions",
                json_body=payload,
                idempotent="client_request_id" in payload,
            )
        except err.InvalidRequestError as e:
            if should_retry_as_polycube(e, auto_request_id, caller_supplied_cube_only):
                self._client._kind_cache[cube_id] = "polycube"
                payload.pop("client_request_id", None)
                response = self._client.request(
                    "POST", "/v1/completions", json_body=payload, idempotent=False
                )
            else:
                raise
        return parse_result(response, cube_id, self._client._kind_cache, self.wait)

    create.__doc__ = CREATE_DOC

    def retrieve(self, request_id: str | uuid.UUID) -> CompletionRecord:
        """Fetch a persisted completion (either kind) by its ``request_id``,
        including the per-attempt ledger for plain cubes and per-node
        ``segments`` for polycubes."""
        response = self._client.request("GET", f"/v1/completions/{request_id}", idempotent=True)
        return CompletionRecord.model_validate(response.json())

    def wait(
        self,
        request_id: str | uuid.UUID,
        *,
        timeout: float = 300.0,
        poll_interval: float | None = None,
    ) -> CompletionRecord:
        deadline = time.monotonic() + timeout
        delay = poll_interval if poll_interval is not None else 0.5
        while True:
            try:
                record = self.retrieve(request_id)
            except err.CompletionNotFoundError:
                record = None
            if record is not None and record.status != "queued":
                if record.status == "error":
                    raise record_error(record)
                return record
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise err.WaitTimeoutError(
                    f"Completion {request_id} was not available after {timeout:g}s",
                    request_id=str(request_id),
                )
            time.sleep(min(delay, remaining))
            delay = _next_poll_delay(delay, poll_interval)

    wait.__doc__ = WAIT_DOC


class AsyncCompletions:
    def __init__(self, client: "AsyncCubic") -> None:
        self._client = client

    async def create(
        self,
        cube_id: str,
        variables: dict[str, Any] | list[dict[str, Any]] | None = None,
        *,
        version: int | None = None,
        history: list[dict[str, str]] | None = None,
        models: list[dict[str, Any]] | None = None,
        parameters: dict[str, Any] | None = None,
        callback_url: str | None = None,
        use_response_cache: bool = True,
        test_mode: bool = False,
        test_response_content: str | dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
        attachments: list[AttachmentInput] | None = None,
        client_request_id: str | uuid.UUID | None = None,
    ) -> CompletionResult | PolycubeResult:
        payload, caller_supplied_cube_only, auto_request_id = build_create_payload(
            cube_id,
            variables,
            version=version,
            history=history,
            models=models,
            parameters=parameters,
            callback_url=callback_url,
            use_response_cache=use_response_cache,
            test_mode=test_mode,
            test_response_content=test_response_content,
            metadata=metadata,
            client_request_id=client_request_id,
            known_polycube=self._client._kind_cache.get(cube_id) == "polycube",
            attachments=attachments,
        )
        try:
            response = await self._client.request(
                "POST",
                "/v1/completions",
                json_body=payload,
                idempotent="client_request_id" in payload,
            )
        except err.InvalidRequestError as e:
            if should_retry_as_polycube(e, auto_request_id, caller_supplied_cube_only):
                self._client._kind_cache[cube_id] = "polycube"
                payload.pop("client_request_id", None)
                response = await self._client.request(
                    "POST", "/v1/completions", json_body=payload, idempotent=False
                )
            else:
                raise
        return parse_result(response, cube_id, self._client._kind_cache, self.wait)

    create.__doc__ = CREATE_DOC

    async def retrieve(self, request_id: str | uuid.UUID) -> CompletionRecord:
        """Fetch a persisted completion (either kind) by its ``request_id``,
        including the per-attempt ledger for plain cubes and per-node
        ``segments`` for polycubes."""
        response = await self._client.request(
            "GET", f"/v1/completions/{request_id}", idempotent=True
        )
        return CompletionRecord.model_validate(response.json())

    async def wait(
        self,
        request_id: str | uuid.UUID,
        *,
        timeout: float = 300.0,
        poll_interval: float | None = None,
    ) -> CompletionRecord:
        deadline = time.monotonic() + timeout
        delay = poll_interval if poll_interval is not None else 0.5
        while True:
            try:
                record = await self.retrieve(request_id)
            except err.CompletionNotFoundError:
                record = None
            if record is not None and record.status != "queued":
                if record.status == "error":
                    raise record_error(record)
                return record
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise err.WaitTimeoutError(
                    f"Completion {request_id} was not available after {timeout:g}s",
                    request_id=str(request_id),
                )
            await asyncio.sleep(min(delay, remaining))
            delay = _next_poll_delay(delay, poll_interval)

    wait.__doc__ = WAIT_DOC
