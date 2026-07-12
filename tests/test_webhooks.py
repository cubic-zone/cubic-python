"""Webhook verification — byte-for-byte parity with the server's signing
scheme in backend/app/workers/callbacks.py."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

import cubic
from cubic import webhooks

from conftest import cube_success_body, polycube_success_body

FALLBACK_SECRET = "server-fallback-secret"
PROJECT_ID = "33333333-3333-3333-3333-333333333333"


def server_sign(body: bytes, project_id: str = PROJECT_ID) -> str:
    """Replicate the backend exactly: project_callback_secret + sign_payload."""
    secret = hmac.new(FALLBACK_SECRET.encode(), project_id.encode(), hashlib.sha256).hexdigest()
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def delivery(payload: dict) -> tuple[bytes, dict, str]:
    body = json.dumps(payload).encode()  # same serialization as callback_delivery
    signature = server_sign(body)
    headers = {
        "Content-Type": "application/json",
        "X-Maxwell-Signature": f"sha256={signature}",
        "X-Maxwell-Request-Id": payload["request_id"],
        "X-Maxwell-Delivery-Attempt": "1",
    }
    secret = webhooks.derive_project_secret(FALLBACK_SECRET, PROJECT_ID)
    return body, headers, secret


def test_verify_parses_cube_delivery():
    body, headers, secret = delivery(cube_success_body("Hello webhook"))
    result = webhooks.verify(body, headers, secret=secret)
    assert result.kind == "cube"
    assert result.content == "Hello webhook"


def test_verify_parses_polycube_delivery():
    body, headers, secret = delivery(polycube_success_body())
    result = webhooks.verify(body, headers, secret=secret)
    assert result.kind == "polycube"
    assert result.content == "chained result"


def test_verify_is_header_case_insensitive():
    body, headers, secret = delivery(cube_success_body())
    lowered = {k.lower(): v for k, v in headers.items()}
    assert webhooks.verify(body, lowered, secret=secret).status == "success"


def test_bare_hex_signature_accepted():
    body, headers, secret = delivery(cube_success_body())
    bare = headers["X-Maxwell-Signature"].removeprefix("sha256=")
    webhooks.verify_signature(body, bare, secret)


def test_tampered_body_rejected():
    body, headers, secret = delivery(cube_success_body())
    tampered = body.replace(b"Hello Ada", b"Hello Eve")
    with pytest.raises(cubic.WebhookSignatureError, match="mismatch"):
        webhooks.verify(tampered, headers, secret=secret)


def test_reserialized_body_rejected():
    """Verification must run on raw bytes — re-serializing changes them."""
    body, headers, secret = delivery(cube_success_body())
    reserialized = json.dumps(json.loads(body), separators=(",", ":")).encode()
    with pytest.raises(cubic.WebhookSignatureError):
        webhooks.verify(reserialized, headers, secret=secret)


def test_missing_signature_header_rejected():
    body, headers, secret = delivery(cube_success_body())
    del headers["X-Maxwell-Signature"]
    with pytest.raises(cubic.WebhookSignatureError, match="Missing"):
        webhooks.verify(body, headers, secret=secret)


def test_wrong_secret_rejected():
    body, headers, _ = delivery(cube_success_body())
    wrong = webhooks.derive_project_secret(FALLBACK_SECRET, "44444444-4444-4444-4444-444444444444")
    with pytest.raises(cubic.WebhookSignatureError):
        webhooks.verify(body, headers, secret=wrong)


def test_error_delivery_does_not_raise():
    """Deliveries are events: an error-status result is returned, not raised."""
    payload = cube_success_body(
        status="error",
        completions=[],
        attempt_errors=[
            {"stage": "llm_call", "error_code": "http_500", "message": "boom", "is_retryable": True}
        ],
    )
    body, headers, secret = delivery(payload)
    result = webhooks.verify(body, headers, secret=secret)
    assert result.status == "error"
    assert result.attempt_errors[0].message == "boom"
