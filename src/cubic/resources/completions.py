"""The completions resource: run a cube (or polycube), fetch and wait on runs.

The sync and async clients share the payload-building, response-parsing, and
error-mapping logic below; the resource classes are thin transport bindings.
"""

from __future__ import annotations

import asyncio
import functools
import re
import time
import uuid
from typing import TYPE_CHECKING, Any, Callable

from .. import _exceptions as err
from ..types import (
    AttemptError,
    CompletionRecord,
    CompletionResult,
    PolycubeResult,
    RenderedPrompt,
)
from .attachments import (
    RETIRED_ATTACHMENTS_MESSAGE,
    abind_file_variables,
    bind_file_variables,
)

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

        ``models`` replaces the cube's stack for this run only — entries as
        listed by ``client.models.list()``, or a model alias
        (``{"provider": "alias", "model_name": "fast-default", "rank": 0}``),
        which resolves against *your* aliases. Overrides are owner-only: a cube
        you subscribe to on the marketplace rejects them outright.

        Files are ordinary inputs: give a file-typed variable a
        :class:`~pathlib.Path`, ``(filename, bytes)``, an
        :class:`~cubic.types.Attachment`, or an ``att_…`` id, and the SDK
        uploads it if needed::

            client.completions.create(cube_id, {"contract": Path("lease.pdf")})

        What happens to the file is the cube's prompt to decide — placed
        directly, read as text with ``<<READ::{{contract}}>>``, transcribed with
        ``<<TRANSCRIBE::{{contract}}>>``. When a file is placed directly, every
        model in the cube's stack must accept it (PDF/images) or the API rejects
        the run with 422 ``attachment_not_supported``.

        A ``client_request_id`` is attached automatically for plain cubes so
        transient failures can be retried without double-executing. When
        ``callback_url`` is set (here or on the cube), the run is queued
        (``result.is_queued``) and the result is delivered to the callback —
        call ``result.wait()`` if you also need it in-process.

        Pass the same ``run_id`` to every call in one workflow and Cubic groups
        them in Logs — including the nested cubes and polycube nodes each call
        sets off, which inherit it. Use whatever key you already have: a job
        name, a date, an order number. Unlike ``client_request_id`` it
        identifies the WORKFLOW rather than the request, so it applies to
        polycubes too::

            client.completions.create(extract_id, {...}, run_id="nightly-2026-08-21")
            client.completions.create(summarise_id, {...}, run_id="nightly-2026-08-21")

        When a run has structure — a job that processes many records, each
        triggering several calls — give ``run_path`` instead, root first. Cubic
        creates whatever levels are missing, so you never declare a run before
        using it, and re-sending the same path is idempotent::

            for record in records:
                client.completions.create(
                    extract_id, {...}, run_path=["nightly-2026-08-21", record.id]
                )

        You can then open the run, see its records, and open a record to see its
        completions. ``run_id`` is simply sugar for a one-level path — sending
        both raises, since they are two spellings of one field.

        ``tags`` are the orthogonal dimension: flat labels, any number per call,
        created the first time you use one. Filtering by several NARROWS, so
        ``urgent`` + ``eu-region`` is the urgent EU work::

            client.completions.create(cube_id, {...}, tags=["urgent", "eu-region"])

        Raises a :class:`~cubic.CompletionError` subclass when the pipeline
        fails, and transport-level :class:`~cubic.CubicError` subclasses for
        auth/validation/quota/HTTP errors.
        """


def build_create_payload(
    cube_id: str,
    variables: dict[str, Any] | list[dict[str, Any]] | None,
    *,
    version: int | None,
    channel: str | None,
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
    run_id: str | None = None,
    run_path: list[str] | None = None,
    tags: list[str] | None = None,
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
    if channel is not None:
        payload["channel"] = channel
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
    if client_request_id is not None:
        payload["client_request_id"] = str(client_request_id)
    elif auto_request_id is not None:
        payload["client_request_id"] = auto_request_id
    # Unlike client_request_id, these are NOT cube-only fields: a polycube
    # accepts them, and grouping the parent plus every node under one run — and
    # under one set of tags — is the point. So they stay out of
    # ``caller_supplied_cube_only`` below and off the list of things the
    # polycube retry has to strip.
    #
    # ``run_id`` and ``run_path`` are two spellings of one field and the API
    # refuses both; sending only what was given keeps that error where it
    # belongs, on the caller who set both.
    if run_id is not None:
        payload["run_id"] = str(run_id)
    if run_path is not None:
        payload["run_path"] = [str(level) for level in run_path]
    if tags is not None:
        payload["tags"] = [str(tag) for tag in tags]

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


def extract_usage(usage: Any) -> dict[str, int | None]:
    """Token counts from a provider's usage object, whatever shape it came in.

    OpenAI reports ``prompt_tokens``/``completion_tokens``; Anthropic reports
    ``input_tokens``/``output_tokens``. Mapping them here rather than server-side
    is deliberate: a provider changing its response shape should cost a client
    upgrade, not a Cubic incident. Dicts and objects both work, and anything
    unrecognised yields ``None`` — unknown, never a silent zero.
    """
    if usage is None:
        return {"input_tokens": None, "output_tokens": None}

    def field(*names: str) -> int | None:
        for name in names:
            value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if isinstance(value, int):
                return value
        return None

    return {
        "input_tokens": field("input_tokens", "prompt_tokens"),
        "output_tokens": field("output_tokens", "completion_tokens"),
    }


def build_render_payload(cube_id: str, variables, **kwargs) -> dict[str, Any]:
    """Render shares the completion request body, minus what execution owns."""
    payload: dict[str, Any] = {"prompt_id": cube_id}
    if variables is not None:
        payload["variables"] = variables
    for key in ("version_number", "channel", "history", "models", "parameters", "metadata"):
        value = kwargs.get(key)
        if value is not None:
            payload[key] = value
    # A render is still a step in a workflow — you fetched a prompt to run it —
    # so it groups and tags with the rest of the run like any other call.
    if kwargs.get("run_id") is not None:
        payload["run_id"] = str(kwargs["run_id"])
    if kwargs.get("run_path") is not None:
        payload["run_path"] = [str(level) for level in kwargs["run_path"]]
    if kwargs.get("tags") is not None:
        payload["tags"] = [str(tag) for tag in kwargs["tags"]]
    if kwargs.get("test_mode"):
        payload["test_mode"] = True
    return payload


def build_attach_payload(
    items: list[dict[str, Any]], metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Scalar form for one unbatched item, ``items`` for everything else — the
    shape the API validates as mutually exclusive.

    ``metadata`` describes the whole attach rather than any one item, so it rides
    alongside either form instead of counting as a scalar field.
    """
    if len(items) == 1 and items[0].get("batch_item_id") is None:
        body = {
            k: v for k, v in items[0].items() if k != "batch_item_id" and v is not None
        } or {"output": items[0].get("output")}
    else:
        body = {"items": items}
    if metadata is not None:
        body["metadata"] = metadata
    return body


class _ExternalRun:
    """The object a ``completions.external(...)`` block yields.

    Set ``output`` (and optionally ``usage``) and the enclosing block attaches on
    exit. If the block raises, it attaches the failure instead and re-raises —
    that automatic capture is the whole reason this is a context manager rather
    than two calls, because a hand-rolled try/finally is what everyone forgets.
    """

    def __init__(self, rendered, attach: Callable[..., Any]) -> None:
        self.rendered = rendered
        self._attach = attach
        self.output: Any = None
        self.usage: Any = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.provider: str | None = rendered.provider
        self.model_name: str | None = rendered.model_name
        self.response_time_ms: int | None = None
        # How you ran it, recorded on the way out. Belongs to the whole block —
        # a batch is still one job of yours — and is attached even when the
        # block raises, which is exactly when knowing how it ran matters most.
        self.metadata: dict[str, Any] | None = None
        self.items: list[_ExternalItem] = [
            _ExternalItem(r, rendered) for r in rendered.renders if r.batch_item_id is not None
        ]

    # Reading the prompt reads through to the render.
    @property
    def messages(self) -> list[dict]:
        return self.rendered.messages

    @property
    def user_prompt(self) -> str:
        return self.rendered.user_prompt

    @property
    def system_instructions(self) -> str | None:
        return self.rendered.system_instructions

    @property
    def response_format(self) -> dict | None:
        return self.rendered.response_format

    @property
    def completion_id(self) -> str:
        return self.rendered.completion_id

    def __iter__(self):
        """Batch renders iterate; a scalar one has nothing to iterate over."""
        if not self.items:
            raise TypeError(
                "This is a scalar render — set `run.output` directly instead of iterating"
            )
        return iter(self.items)

    def _payload_items(self, error: BaseException | None) -> list[dict[str, Any]]:
        if error is not None:
            message = f"{type(error).__name__}: {error}"
            if self.items:
                return [
                    {"batch_item_id": i.batch_item_id, "status": "error", "error": message}
                    for i in self.items
                ]
            return [{"status": "error", "error": message}]
        if self.items:
            return [i._payload() for i in self.items]
        tokens = extract_usage(self.usage)
        return [
            {
                "batch_item_id": None,
                "output": self.output,
                "provider": self.provider,
                "model_name": self.model_name,
                "input_tokens": (
                    self.input_tokens if self.input_tokens is not None else tokens["input_tokens"]
                ),
                "output_tokens": (
                    self.output_tokens
                    if self.output_tokens is not None
                    else tokens["output_tokens"]
                ),
                "response_time_ms": self.response_time_ms,
                "status": "success",
            }
        ]


class _ExternalItem:
    """One item of a batch render inside an ``external`` block."""

    def __init__(self, item, rendered) -> None:
        self._item = item
        self.batch_item_id = item.batch_item_id
        self.output: Any = None
        self.usage: Any = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.provider: str | None = rendered.provider
        self.model_name: str | None = rendered.model_name
        self.response_time_ms: int | None = None

    @property
    def messages(self) -> list[dict]:
        return self._item.messages

    @property
    def user_prompt(self) -> str:
        return self._item.user_prompt

    @property
    def system_instructions(self) -> str | None:
        return self._item.system_instructions

    def _payload(self) -> dict[str, Any]:
        tokens = extract_usage(self.usage)
        return {
            "batch_item_id": self.batch_item_id,
            "output": self.output,
            "provider": self.provider,
            "model_name": self.model_name,
            "input_tokens": (
                self.input_tokens if self.input_tokens is not None else tokens["input_tokens"]
            ),
            "output_tokens": (
                self.output_tokens if self.output_tokens is not None else tokens["output_tokens"]
            ),
            "response_time_ms": self.response_time_ms,
            "status": "success",
        }


class _ExternalContext:
    """Sync ``with`` wrapper around a render + attach pair."""

    def __init__(self, render: Callable[[], Any], attach: Callable[..., Any]) -> None:
        self._render = render
        self._attach = attach
        self._run: _ExternalRun | None = None

    def __enter__(self) -> _ExternalRun:
        self._run = _ExternalRun(self._render(), self._attach)
        return self._run

    def __exit__(self, exc_type, exc, tb) -> bool:
        run = self._run
        if run is None:  # pragma: no cover - __enter__ always sets it
            return False
        self._attach(run.completion_id, run._payload_items(exc), run.metadata)
        return False  # never swallow: the caller's exception is theirs to see


class _AsyncExternalContext:
    """``async with`` twin of :class:`_ExternalContext`."""

    def __init__(self, render, attach) -> None:
        self._render = render
        self._attach = attach
        self._run: _ExternalRun | None = None

    async def __aenter__(self) -> _ExternalRun:
        self._run = _ExternalRun(await self._render(), self._attach)
        return self._run

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        run = self._run
        if run is None:  # pragma: no cover
            return False
        await self._attach(run.completion_id, run._payload_items(exc), run.metadata)
        return False


EXTERNAL_DOC = """Render a cube's prompt, run it yourself, and post the result back.

        For using Cubic's prompt management with your own inference. The block
        yields the rendered prompt plus the model stack and schema needed to
        reproduce the call; set ``output`` and it is attached on exit, so the run
        still lands in Logs, Usage and per-version outcomes::

            with client.completions.external(cube_id, {"inquiry": text}) as run:
                resp = openai.chat.completions.create(
                    model=run.model_name, messages=run.messages
                )
                run.output = resp.choices[0].message.content
                run.usage = resp.usage      # tokens mapped for you

        If the block raises, the failure is attached and re-raised, so external
        error rates stay visible instead of looking like runs nobody finished.

        A batch render iterates instead, and attaches every item in one call::

            with client.completions.external(cube_id, [{"id": "a", ...}]) as batch:
                for item in batch:
                    item.output = run_your_model(item.messages)

        Rendering is billed for what it consumed — function markers, resources
        and nested cubes all still execute — but the attach itself is free.
        Requires a paid plan, and is refused for marketplace cubes you don't own
        and for polycubes.
        """


class Completions:
    def __init__(self, client: "Cubic") -> None:
        self._client = client

    def render(
        self,
        cube_id: str,
        variables: dict[str, Any] | list[dict[str, Any]] | None = None,
        *,
        version: int | None = None,
        channel: str | None = None,
        history: list[dict[str, str]] | None = None,
        models: list[dict[str, Any]] | None = None,
        parameters: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        test_mode: bool = False,
        run_id: str | None = None,
        run_path: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> RenderedPrompt:
        """The cube's prompt, substituted and ready to run, without running it.

        Returns a ``completion_id`` you can attach a result to. To read the
        prompt as *written* — unsubstituted, free, no record — use
        ``client.cubes.retrieve()`` instead.
        """
        variables = bind_file_variables(variables, self._client.attachments.upload)
        payload = build_render_payload(
            cube_id,
            variables,
            version_number=version,
            channel=channel,
            history=history,
            models=models,
            parameters=parameters,
            metadata=metadata,
            test_mode=test_mode,
            run_id=run_id,
            run_path=run_path,
            tags=tags,
        )
        response = self._client.request("POST", "/v1/completions/render", json_body=payload)
        return RenderedPrompt.model_validate(response.json())

    def attach(
        self,
        completion_id: str | uuid.UUID,
        items: list[dict[str, Any]] | None = None,
        *,
        output: Any = None,
        provider: str | None = None,
        model_name: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        usage: Any = None,
        response_time_ms: int | None = None,
        status: str = "success",
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CompletionRecord:
        """Post back the result of a render you executed yourself.

        Free — the run was billed at render. Supply ``provider``/``model_name``
        and token counts (or a provider ``usage`` object) and Cubic prices the
        call from its model catalog, so inference you paid for directly still
        shows true cost. Attaching twice raises.

        ``metadata`` records how you actually ran it — the endpoint you hit, the
        parameters you chose, your own request id. It is stored separately from
        the ``metadata`` you passed to ``render``, so stating intent up front and
        reporting execution afterwards never overwrite each other.
        """
        if items is None:
            tokens = extract_usage(usage)
            items = [
                {
                    "batch_item_id": None,
                    "output": output,
                    "provider": provider,
                    "model_name": model_name,
                    "input_tokens": (
                        input_tokens if input_tokens is not None else tokens["input_tokens"]
                    ),
                    "output_tokens": (
                        output_tokens if output_tokens is not None else tokens["output_tokens"]
                    ),
                    "response_time_ms": response_time_ms,
                    "status": status,
                    "error": error,
                }
            ]
        response = self._client.request(
            "POST",
            f"/v1/completions/{completion_id}/response",
            json_body=build_attach_payload(items, metadata),
        )
        return CompletionRecord.model_validate(response.json())

    def external(self, cube_id: str, variables=None, **kwargs) -> _ExternalContext:
        return _ExternalContext(
            lambda: self.render(cube_id, variables, **kwargs),
            lambda cid, items, meta=None: self.attach(cid, items, metadata=meta),
        )

    external.__doc__ = EXTERNAL_DOC

    def external_handler(self, cube_id: str, **kwargs):
        """Decorator sugar over :meth:`external` for a function that IS the call.

        The decorated function receives the run as its first argument and is
        called with the cube's variables as keyword arguments; whatever it
        returns becomes the attached output::

            @client.completions.external_handler("cbe_a1B2c3D4e5F6g7")
            def handle(run, *, inquiry: str) -> str:
                resp = openai.chat.completions.create(
                    model=run.model_name, messages=run.messages
                )
                run.usage = resp.usage          # optional, for true cost
                return resp.choices[0].message.content

            handle(inquiry="my order is late")

        Prefer :meth:`external` when the variables are computed rather than
        parameters, or when the cube isn't known at import time — a decorator
        fixes it there.
        """

        def decorate(fn):
            @functools.wraps(fn)
            def wrapper(**variables):
                with self.external(cube_id, variables or None, **kwargs) as run:
                    result = fn(run, **variables)
                    # An explicit `run.output` wins; returning the text is just
                    # the shorter way to say the same thing.
                    if run.output is None and result is not None:
                        run.output = result
                    return result

            return wrapper

        return decorate

    def create(
        self,
        cube_id: str,
        variables: dict[str, Any] | list[dict[str, Any]] | None = None,
        *,
        version: int | None = None,
        channel: str | None = None,
        history: list[dict[str, str]] | None = None,
        models: list[dict[str, Any]] | None = None,
        parameters: dict[str, Any] | None = None,
        callback_url: str | None = None,
        use_response_cache: bool = True,
        test_mode: bool = False,
        test_response_content: str | dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
        attachments: Any = None,  # retired — see RETIRED_ATTACHMENTS_MESSAGE
        client_request_id: str | uuid.UUID | None = None,
        run_id: str | None = None,
        run_path: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> CompletionResult | PolycubeResult:
        if attachments:
            raise err.CubicError(RETIRED_ATTACHMENTS_MESSAGE)
        variables = bind_file_variables(variables, self._client.attachments.upload)
        payload, caller_supplied_cube_only, auto_request_id = build_create_payload(
            cube_id,
            variables,
            version=version,
            channel=channel,
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
            run_id=run_id,
            run_path=run_path,
            tags=tags,
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

    async def render(
        self,
        cube_id: str,
        variables: dict[str, Any] | list[dict[str, Any]] | None = None,
        *,
        version: int | None = None,
        channel: str | None = None,
        history: list[dict[str, str]] | None = None,
        models: list[dict[str, Any]] | None = None,
        parameters: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        test_mode: bool = False,
        run_id: str | None = None,
        run_path: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> RenderedPrompt:
        """The cube's prompt, substituted and ready to run, without running it."""
        variables = await abind_file_variables(variables, self._client.attachments.upload)
        payload = build_render_payload(
            cube_id,
            variables,
            version_number=version,
            channel=channel,
            history=history,
            models=models,
            parameters=parameters,
            metadata=metadata,
            test_mode=test_mode,
            run_id=run_id,
            run_path=run_path,
            tags=tags,
        )
        response = await self._client.request(
            "POST", "/v1/completions/render", json_body=payload
        )
        return RenderedPrompt.model_validate(response.json())

    async def attach(
        self,
        completion_id: str | uuid.UUID,
        items: list[dict[str, Any]] | None = None,
        *,
        output: Any = None,
        provider: str | None = None,
        model_name: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        usage: Any = None,
        response_time_ms: int | None = None,
        status: str = "success",
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CompletionRecord:
        """Post back the result of a render you executed yourself."""
        if items is None:
            tokens = extract_usage(usage)
            items = [
                {
                    "batch_item_id": None,
                    "output": output,
                    "provider": provider,
                    "model_name": model_name,
                    "input_tokens": (
                        input_tokens if input_tokens is not None else tokens["input_tokens"]
                    ),
                    "output_tokens": (
                        output_tokens if output_tokens is not None else tokens["output_tokens"]
                    ),
                    "response_time_ms": response_time_ms,
                    "status": status,
                    "error": error,
                }
            ]
        response = await self._client.request(
            "POST",
            f"/v1/completions/{completion_id}/response",
            json_body=build_attach_payload(items, metadata),
        )
        return CompletionRecord.model_validate(response.json())

    def external(self, cube_id: str, variables=None, **kwargs) -> _AsyncExternalContext:
        return _AsyncExternalContext(
            lambda: self.render(cube_id, variables, **kwargs),
            lambda cid, items, meta=None: self.attach(cid, items, metadata=meta),
        )

    external.__doc__ = EXTERNAL_DOC

    def external_handler(self, cube_id: str, **kwargs):
        """Awaitable twin of ``Completions.external_handler``."""

        def decorate(fn):
            @functools.wraps(fn)
            async def wrapper(**variables):
                async with self.external(cube_id, variables or None, **kwargs) as run:
                    result = await fn(run, **variables)
                    if run.output is None and result is not None:
                        run.output = result
                    return result

            return wrapper

        return decorate

    async def create(
        self,
        cube_id: str,
        variables: dict[str, Any] | list[dict[str, Any]] | None = None,
        *,
        version: int | None = None,
        channel: str | None = None,
        history: list[dict[str, str]] | None = None,
        models: list[dict[str, Any]] | None = None,
        parameters: dict[str, Any] | None = None,
        callback_url: str | None = None,
        use_response_cache: bool = True,
        test_mode: bool = False,
        test_response_content: str | dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
        attachments: Any = None,  # retired — see RETIRED_ATTACHMENTS_MESSAGE
        client_request_id: str | uuid.UUID | None = None,
        run_id: str | None = None,
        run_path: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> CompletionResult | PolycubeResult:
        if attachments:
            raise err.CubicError(RETIRED_ATTACHMENTS_MESSAGE)
        variables = await abind_file_variables(variables, self._client.attachments.upload)
        payload, caller_supplied_cube_only, auto_request_id = build_create_payload(
            cube_id,
            variables,
            version=version,
            channel=channel,
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
            run_id=run_id,
            run_path=run_path,
            tags=tags,
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
