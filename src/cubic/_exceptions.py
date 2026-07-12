"""Exception hierarchy for the Cubic SDK.

Every exception carries ``error_code`` (the API's machine-readable code),
``status_code`` (HTTP, when the error came from a non-2xx response),
``request_id`` (the ``X-Request-ID`` correlation header — quote it in support
requests), and ``body`` (the raw parsed response body, when available).

Pipeline failures — where the API returns HTTP 200 with ``status: "error"`` —
are raised as :class:`CompletionError` subclasses and additionally carry the
parsed result object on ``.result``.
"""

from __future__ import annotations

from typing import Any


class CubicError(Exception):
    """Base class for every error raised by the Cubic SDK."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        status_code: int | None = None,
        request_id: str | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.request_id = request_id
        self.body = body


class APIConnectionError(CubicError):
    """The SDK could not reach the Cubic API (DNS, refused connection, TLS…)."""


class APITimeoutError(APIConnectionError):
    """The HTTP request timed out client-side before a response arrived."""


class AuthenticationError(CubicError):
    """401 — the API key is missing, invalid, revoked, or expired."""


class PermissionDeniedError(CubicError):
    """403 — e.g. ``marketplace_subscription_required`` or ``override_forbidden``."""


class NotFoundError(CubicError):
    """404 — the referenced resource does not exist (or is not yours)."""


class CubeNotFoundError(NotFoundError):
    """The cube/polycube ID could not be resolved.

    The API deliberately returns the same response for an unknown ID and for a
    cube your key does not own, so foreign IDs cannot be probed. Note that
    marketplace subscribers can *run* a listed cube but cannot read its
    definition, and ``cubes.retrieve`` does not (yet) serve polycube IDs.
    """


class VersionNotFoundError(CubeNotFoundError):
    """The cube exists but the pinned ``version`` does not."""


class CompletionNotFoundError(NotFoundError):
    """No completion record exists for the given ``request_id``."""


class ModelNotFoundError(NotFoundError):
    """No catalog model matches the given name (client-side lookup against
    ``models.list()``). The message includes close-match suggestions."""


class InvalidRequestError(CubicError):
    """422 — the request body failed validation (unknown fields, bad types,
    out-of-range parameters, or fields not applicable to a polycube)."""


class InsufficientCreditsError(CubicError):
    """The credit gate rejected the request. Not retryable — top up first."""

    def __init__(
        self,
        message: str,
        *,
        required: int | None = None,
        balance: int | None = None,
        grace: int | None = None,
        topup_allowed: bool | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.required = required
        self.balance = balance
        self.grace = grace
        self.topup_allowed = topup_allowed


class RateLimitError(CubicError):
    """429 — the server is at capacity or a quota was exceeded.

    Capacity 429s are retried automatically; if you see this, retries were
    exhausted. ``retry_after`` echoes the server's ``Retry-After`` header.
    """

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class CompletionTimeoutError(CubicError):
    """504 — the completion exceeded the server's execution deadline."""


class InternalServerError(CubicError):
    """5xx — an unexpected server-side failure."""


class WaitTimeoutError(CubicError):
    """``wait()`` gave up before the queued completion's result was persisted.

    The run may still complete server-side — the ``request_id`` stays valid for
    ``completions.retrieve`` and the callback delivery is unaffected.
    """


class WebhookSignatureError(CubicError):
    """The callback payload's ``X-Maxwell-Signature`` header is missing or does
    not match the payload — treat the delivery as unauthenticated."""


class CompletionError(CubicError):
    """The request was accepted but the completion pipeline failed
    (HTTP 200 with ``status: "error"``).

    ``result`` holds the fully parsed response — including ``attempt_errors``
    and, for polycubes, per-node ``segments`` — for inspection.
    """

    def __init__(
        self,
        message: str,
        *,
        result: Any = None,
        attempt_errors: list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.result = result
        self.attempt_errors = attempt_errors or []


class MissingVariableError(CompletionError):
    """A required template variable was not provided (or failed coercion)."""

    def __init__(self, message: str, *, variable_name: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.variable_name = variable_name


class ProviderError(CompletionError):
    """Every model attempt failed at the provider stage (rate limits, outages,
    circuit breaker open, missing provider credential…). Check
    ``attempt_errors`` for the per-attempt detail, including ``is_retryable``."""
