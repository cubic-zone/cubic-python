"""Typed response models for the Cubic SDK.

These mirror the API's wire contracts but parse tolerantly (unknown fields are
ignored) so older SDK versions keep working as the API grows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, PrivateAttr

from ._exceptions import CubicError


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class AttemptError(_Model):
    """One error from the completion pipeline (per attempt or chain-level)."""

    stage: str
    provider: str | None = None
    model: str | None = None
    error_code: str | None = None
    message: str
    is_retryable: bool = False
    timestamp: datetime | None = None


class Metrics(_Model):
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost: float = 0.0
    response_time_ms: int = 0
    llm_response_time_ms: int | None = None
    model_used: str | None = None
    provider_used: str | None = None
    success: bool = True
    attempt_count: int = 1
    validation_retries: int = 0
    models_tried: list[str] = []
    cache_hit: bool = False
    prompt_cache_hit: bool = False
    credits_charged: int = 0


class SingleCompletion(_Model):
    completion_id: uuid.UUID
    content: str | dict | None = None
    model_used: str | None = None
    provider_used: str | None = None
    completion_type: str
    is_winner: bool | None = None
    batch_item_id: str | None = None
    metrics: Metrics
    error: AttemptError | None = None


class _ResultBase(_Model):
    request_id: uuid.UUID
    attempt_errors: list[AttemptError] = []
    overall_metrics: Metrics
    task_id: uuid.UUID | None = None

    # Injected by the resource that produced this result so ``wait()`` can poll.
    _waiter: Any = PrivateAttr(default=None)

    @property
    def metrics(self) -> Metrics:
        return self.overall_metrics

    @property
    def is_queued(self) -> bool:
        """True when the run was accepted for async execution (202). Poll
        ``client.completions.retrieve(result.request_id)``, call ``wait()``,
        or receive the callback delivery."""
        return self.status == "queued"  # type: ignore[attr-defined]

    def wait(self, *, timeout: float = 300.0, poll_interval: float | None = None) -> Any:
        """Poll until this run's persisted result is available and return the
        :class:`CompletionRecord`. Equivalent to
        ``client.completions.wait(result.request_id)``.

        With :class:`~cubic.AsyncCubic` this returns a coroutine — ``await`` it.
        """
        if self._waiter is None:
            raise RuntimeError(
                "wait() is only available on results produced by a client "
                "(this instance was constructed manually)"
            )
        return self._waiter(self.request_id, timeout=timeout, poll_interval=poll_interval)


class CompletionResult(_ResultBase):
    """Result of running a plain cube."""

    status: Literal["success", "partial", "error", "queued"]
    completions: list[SingleCompletion] = []
    attempts: list[SingleCompletion] = []
    merged_content: str | float | dict | None = None

    kind: Literal["cube"] = "cube"

    @property
    def is_partial(self) -> bool:
        """True when content was delivered but some fallback attempts failed
        (details in ``attempt_errors`` / ``attempts``)."""
        return self.status == "partial"

    @property
    def is_batch(self) -> bool:
        """True when this result came from a batch-variables run."""
        items = self.completions or self.attempts
        return any(c.batch_item_id is not None for c in items)

    @property
    def content(self) -> str | float | dict | None:
        """The delivered content: ``merged_content`` when the strategy merges,
        otherwise the winning completion's content.

        Raises for batch results — a batch has no single winner; use
        ``contents`` (keyed by your batch item ids) or iterate ``completions``.
        """
        if self.is_batch:
            raise CubicError(
                "This is a batch result with no single content — use "
                "result.contents (a dict keyed by your batch item ids) or "
                "iterate result.completions."
            )
        if self.merged_content is not None:
            return self.merged_content
        for c in self.completions:
            if c.is_winner:
                return c.content
        if self.completions:
            return self.completions[0].content
        return None

    @property
    def contents(self) -> dict[str | None, str | float | dict | None]:
        """Batch outputs keyed by the ``id`` of each submitted batch item.

        Contains the successfully delivered items; on a partial batch, failed
        items are absent here — find them (with errors) in ``attempts`` by
        ``batch_item_id``. Raises for non-batch results; use ``content``.
        """
        if not self.is_batch:
            raise CubicError("Not a batch result — use result.content.")
        return {c.batch_item_id: c.content for c in self.completions}


class Segment(_Model):
    """One polycube node's result."""

    node_key: str
    cube_id: str
    version_number: int
    status: Literal["success", "partial", "error", "skipped"]
    output: str | float | dict | list | None = None
    request_id: uuid.UUID | None = None
    metrics: Metrics | None = None
    error: AttemptError | None = None


class PolycubeResult(_ResultBase):
    """Result of running a polycube (a chained cube)."""

    status: Literal["success", "error", "queued"]
    chain_id: str
    segments: list[Segment] = []
    final_output: str | float | dict | list | None = None

    kind: Literal["polycube"] = "polycube"

    @property
    def cube_id(self) -> str:
        return self.chain_id

    @property
    def is_partial(self) -> bool:
        return False

    @property
    def content(self) -> str | float | dict | list | None:
        """The final node's output."""
        return self.final_output


class CompletionRecord(_Model):
    """A persisted completion fetched by ``request_id``.

    The record shape differs slightly between kinds; fields not applicable to
    the record's kind are ``None``. Extra server fields are preserved via
    ``model_extra``.
    """

    model_config = ConfigDict(extra="allow")

    request_id: str
    status: str
    kind: str = "cube"
    cube_id: str | None = None
    # plain-cube records
    prompt_id: str | None = None
    response: dict | None = None
    model_used: str | None = None
    provider_used: str | None = None
    attempts: list[dict] = []
    # polycube records
    segments: list[dict] | None = None
    final_output: str | float | dict | list | None = None
    # shared
    variables_used: dict | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_cost: float | None = None
    response_time_ms: int | None = None
    error_detail: dict | list[dict] | None = None
    created_at: str | None = None


class Model(_Model):
    """One model from the public catalog (``GET /v1/models``).

    ``model_name`` is the provider call-string used in ``models=`` overrides
    and cube model stacks; ``display_name`` is the human-facing label.
    """

    provider: str
    model_name: str
    owner: str | None = None
    display_name: str | None = None
    context_window: int | None = None
    supports_reasoning: bool = False
    supports_structured_output: bool = False
    supports_temperature: bool = True
    supports_web_search: bool = False
    supports_tools: bool = False
    input_per_1k: float | None = None
    output_per_1k: float | None = None


class CubeModel(_Model):
    """One entry of a cube's model stack."""

    provider: str
    model_name: str
    rank: int
    role: str


class CubeVersion(_Model):
    """One saved version of a cube's content.

    ``version_number`` is the internal monotonic sequence (pin/rollback by it);
    ``version`` is the human-facing semantic label the server sizes to the
    content delta (``change_ratio``: 0 = identical to the previous version,
    1 = fully different).
    """

    version_number: int
    version: str
    change_ratio: float | None = None
    is_current: bool = False
    created_at: datetime | None = None


class Attachment(_Model):
    """An uploaded attachment (``POST /v1/attachments``).

    ``id`` is the reusable ``att_…`` reference for ``completions.create``.
    ``tier`` is how the platform will handle it: ``native`` (forwarded to the
    model as a multimodal part — PDF/images), ``text`` (injected into the
    prompt — MD/TXT/RTF/SVG), or ``extraction`` (text-extracted server-side —
    DOCX/PPTX/XLSX). The type is sniffed from the file's bytes, never from its
    name. Bytes are retained for 7 days (``expires_at``); the id keeps working
    for re-runs until then."""

    id: str
    filename: str
    media_type: str
    tier: Literal["text", "native", "extraction"]
    size_bytes: int
    sha256: str
    status: str
    expires_at: datetime
    created_at: datetime | None = None


class Project(_Model):
    """One of your projects (``GET /v1/projects``) — a placement target for
    cubes. ``project_id`` is the public ``prj_…`` id used on ``cubes.create``."""

    project_id: str
    name: str
    description: str | None = None
    is_marketplace: bool = False


class PolycubeNode(_Model):
    """One node of a polycube graph — a cube plus its optional version pin."""

    node_key: str
    cube_id: str
    title: str | None = None
    completion_type: str | None = None
    version_number: int | None = None  # the pin; None = follows current
    resolved_version: int | None = None
    version_label: str | None = None
    variables: dict = {}
    output_fields: list[dict] | None = None  # None = text-only output port
    position_x: float = 0
    position_y: float = 0


class PolycubeEdge(_Model):
    """One field→variable mapping between two nodes."""

    source_node_key: str
    target_node_key: str
    source_field: str | None = None  # None = the source node's whole output
    target_variable: str


class PolycubeInput(_Model):
    """One entry of the polycube's derived input signature — a variable a run
    must supply because no edge fills it."""

    name: str
    type: str = "string"
    required: bool = True
    description: str | None = None
    consumers: list[dict] = []


class Polycube(_Model):
    """A polycube definition (owner-only) — the graph plus its derived inputs."""

    polycube_id: str
    title: str
    description: str | None = None
    callback_url: str | None = None
    nodes: list[PolycubeNode] = []
    edges: list[PolycubeEdge] = []
    inputs: list[PolycubeInput] = []
    warnings: list[str] = []


class Cube(_Model):
    """A cube definition (owner-only read surface).

    ``kind`` is reported for forward compatibility; today this endpoint only
    serves plain cubes — polycube definitions are not yet readable by API key.
    """

    cube_id: str
    title: str
    kind: str = "cube"
    completion_type: str
    callback_url: str | None = None
    parameters: dict = {}
    merge_responses: bool = False
    current_version: int
    version_number: int
    system_instructions: str | None = None
    user_prompt: str
    variables: dict = {}
    functions: list[str] = []
    response_format: dict | None = None
    response_format_source: str = "none"
    models: list[CubeModel] = []
