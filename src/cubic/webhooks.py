"""Verify and parse Cubic callback (webhook) deliveries.

When a run has a ``callback_url``, Cubic POSTs the full result to it once the
worker finishes. Every delivery is signed:

- ``X-Maxwell-Signature: sha256=<hex>`` — HMAC-SHA256 of the raw request body
  using your project's callback signing secret
- ``X-Maxwell-Request-Id`` — the run's ``request_id``
- ``X-Maxwell-Delivery-Attempt`` — 1-based delivery attempt (retries reuse the
  same body and signature)

Typical handler::

    from cubic import webhooks

    result = webhooks.verify(request.body, request.headers, secret=SIGNING_SECRET)
    if result.status == "success":
        handle(result.content)

Always verify against the *raw* request body bytes — re-serializing the JSON
will change the byte sequence and fail verification.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping

from ._exceptions import WebhookSignatureError
from .types import CompletionResult, PolycubeResult

SIGNATURE_HEADER = "X-Maxwell-Signature"
REQUEST_ID_HEADER = "X-Maxwell-Request-Id"
DELIVERY_ATTEMPT_HEADER = "X-Maxwell-Delivery-Attempt"


def compute_signature(body: bytes | str, secret: str) -> str:
    """The hex HMAC-SHA256 signature Cubic computes for ``body``."""
    if isinstance(body, str):
        body = body.encode()
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes | str, signature: str | None, secret: str) -> None:
    """Check a delivery's signature; raises :class:`~cubic.WebhookSignatureError`.

    ``signature`` is the ``X-Maxwell-Signature`` header value — either the full
    ``sha256=<hex>`` form or the bare hex digest.
    """
    if not signature:
        raise WebhookSignatureError(f"Missing {SIGNATURE_HEADER} header")
    presented = signature.strip()
    if presented.startswith("sha256="):
        presented = presented[len("sha256="):]
    expected = compute_signature(body, secret)
    if not hmac.compare_digest(presented.lower(), expected):
        raise WebhookSignatureError(
            "Webhook signature mismatch — the payload was not signed with this "
            "secret (wrong secret, or the body was modified/re-serialized in transit)"
        )


def parse(body: bytes | str) -> CompletionResult | PolycubeResult:
    """Parse a delivery body into a typed result WITHOUT verifying it.

    Deliveries carry the same envelope the create call returns, so this yields
    a :class:`CompletionResult` or :class:`PolycubeResult`. Unlike
    ``completions.create``, an ``error`` status does not raise — a delivery is
    an event you inspect, so check ``result.status`` yourself.
    """
    data = json.loads(body)
    if isinstance(data, dict) and "chain_id" in data:
        return PolycubeResult.model_validate(data)
    return CompletionResult.model_validate(data)


def verify(
    body: bytes | str,
    headers: Mapping[str, Any],
    *,
    secret: str,
) -> CompletionResult | PolycubeResult:
    """Verify a delivery's signature and parse its body in one step.

    ``headers`` is the incoming request's header mapping (case-insensitive
    lookup is handled here). Raises :class:`~cubic.WebhookSignatureError` when
    the signature is missing or wrong; otherwise returns the parsed result.
    """
    signature = None
    for key, value in headers.items():
        if str(key).lower() == SIGNATURE_HEADER.lower():
            signature = str(value)
            break
    verify_signature(body, signature, secret)
    return parse(body)


def derive_project_secret(fallback_secret: str, project_id: str) -> str:
    """Derive a project's signing secret the way the server does.

    Only useful for self-hosted deployments where you hold the server's
    ``CALLBACK_FALLBACK_SECRET``; hosted users receive their per-project
    signing secret from the dashboard.
    """
    return hmac.new(fallback_secret.encode(), project_id.encode(), hashlib.sha256).hexdigest()
