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


class GeneratedFile(_Model):
    """One binary artifact a Binary Cube generated (image/audio).

    ``url`` is a short-lived presigned download link (about an hour) — for
    durable access use ``client.files.download(file)`` / ``save()``, which work
    for the file's whole 30-day retention. After retention, ``status`` reads
    ``"expired"``: the metadata stays, the bytes are gone. Test-mode runs return
    a built-in stub (``test_stub=True``, ``id=None``, data-URI ``url``)."""

    id: str | None = None  # fil_… (None for test-mode stubs)
    kind: str | None = None  # image | audio | video
    media_type: str
    size_bytes: int | None = None
    sha256: str | None = None
    url: str | None = None
    status: str | None = None  # "active" | "expired" (populated on record reads)
    expires_at: datetime | None = None
    test_stub: bool = False

    @property
    def is_expired(self) -> bool:
        return self.status == "expired"


def _files_of(content: Any) -> list[GeneratedFile]:
    """Parse a completion's content into GeneratedFiles (empty for text)."""
    if not isinstance(content, dict):
        return []
    if isinstance(content.get("file"), dict):
        return [GeneratedFile.model_validate(content["file"])]
    if isinstance(content.get("files"), list):
        return [GeneratedFile.model_validate(f) for f in content["files"] if isinstance(f, dict)]
    return []


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

    @property
    def files(self) -> list[GeneratedFile]:
        """Generated binary artifacts of this completion (empty for text)."""
        return _files_of(self.content)


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

    @property
    def files(self) -> list[GeneratedFile]:
        """Every generated binary artifact across the delivered completions
        (Binary Cubes) — one per output for broadcast/batch runs, usually one
        for fallback. Empty for text cubes."""
        out: list[GeneratedFile] = []
        for c in self.completions:
            if c.error is None:
                out.extend(c.files)
        return out

    @property
    def file(self) -> GeneratedFile | None:
        """THE generated file of a single-output binary run (fallback cubes).

        ``None`` for text results; raises when the run produced several files
        (broadcast/batch) — use ``files`` there."""
        files = self.files
        if not files:
            return None
        if len(files) > 1:
            raise CubicError(
                f"This run produced {len(files)} files — use result.files."
            )
        return files[0]


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


class BumpReason(_Model):
    """One rule that fired when the server sized the semantic bump.

    ``rule`` is stable and machine-readable (``rank0_model_changed``,
    ``schema_field_removed``, …); ``message`` is the human sentence.
    """

    rule: str
    level: str  # major | minor | patch
    message: str
    detail: dict[str, Any] = {}


class CubeVersion(_Model):
    """One published version — an immutable snapshot of content AND config.

    ``version_number`` is the internal monotonic sequence (pin by it);
    ``version`` is the human-facing semantic label the server sizes to the whole
    delta, content *and* config. ``bump_reason`` lists every rule that produced
    it, so a major is never an unexplained number.

    ``channels`` are the named pointers aimed here right now — ``["production"]``
    means this is what an unqualified completion runs.
    """

    version_number: int
    version: str
    change_ratio: float | None = None
    is_current: bool = False
    channels: list[str] = []
    change_note: str | None = None
    change_note_source: str = "none"  # human | generated | none
    author: str | None = None
    bump_reason: list[BumpReason] = []
    # True for versions predating config versioning: the config recorded is
    # today's, not necessarily what ran at the time.
    config_backfilled: bool = False
    created_at: datetime | None = None


class Channel(_Model):
    """A named, movable pointer from a cube to a version.

    What Langfuse and PromptLayer call a *label* and Agenta, Helicone and
    Braintrust call an *environment*. ``production`` exists on every cube and is
    what a completion resolves to by default; ``latest`` is reserved
    (``reserved=True``), always the highest version, and cannot be moved.
    """

    name: str
    version_number: int
    semantic_label: str | None = None
    protected: bool = False
    reserved: bool = False
    updated_at: datetime | None = None


class ChangelogEvent(_Model):
    """One entry on a cube's timeline, or the account-wide feed.

    ``event_type`` is one of ``version.published``, ``channel.created``,
    ``channel.moved``, ``channel.deleted``, ``version.rolled_back``. A rollback
    names the version that was **pulled** and how long it served.
    """

    id: int
    event_type: str
    entity_type: str
    entity_id: str
    project_id: str | None = None
    actor_name: str | None = None
    payload: dict[str, Any] = {}
    created_at: datetime | None = None


class CubeDraft(_Model):
    """Staged, unpublished changes to a cube.

    Nothing here serves traffic. ``bump`` is what publishing would produce —
    ``bump["level"]`` and ``bump["next_label"]`` — computed without publishing.
    """

    prompt_id: str
    has_draft: bool = False
    base_version_number: int | None = None
    # True when someone published while this draft was open.
    base_is_stale: bool = False
    content: dict[str, Any] = {}
    config: dict[str, Any] = {}
    changed_fields: list[str] = []
    bump: dict[str, Any] = {}
    updated_at: datetime | None = None


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
    # True when an ``update`` landed on your draft rather than applying live.
    # The definition above is still the PUBLISHED one — read ``cubes.draft()``
    # to see what is staged, and ``cubes.publish()`` to ship it.
    draft_pending: bool = False
    draft_changed_fields: list[str] = []


VariableType = Literal["string", "integer", "float", "boolean", "file"]


def variable(
    type: VariableType = "string",
    *,
    required: bool = True,
    description: str | None = None,
) -> dict[str, Any]:
    """Declare one of a cube's inputs, for ``variables=`` on ``cubes.create`` /
    ``create_version`` / ``test``.

    ::

        variables={
            "contract": variable("file", description="The signed agreement"),
            "focus": variable(required=False),
        }

    A name you never declare is auto-detected from the ``{{placeholders}}`` in
    the content and defaults to a required string, so you only declare what
    differs. Declaring ``"file"`` is what lets a variable take an uploaded file:
    an ``att_`` id in any other variable is rejected rather than dereferenced,
    since variables routinely carry text from your own end users.
    """
    declaration: dict[str, Any] = {"type": type, "required": required}
    if description is not None:
        declaration["description"] = description
    return declaration
