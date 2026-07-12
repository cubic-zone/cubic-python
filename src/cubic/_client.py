"""The synchronous Cubic client: transport, auth, retries, and error mapping."""

from __future__ import annotations

import os
import random
import time
from typing import Any

import httpx

from . import _exceptions as err
from ._version import __version__

# The hosted Cubic API. Point CUBIC_BASE_URL or base_url= at another deployment
# (e.g. http://localhost:8010 for local development).
DEFAULT_BASE_URL = "https://api.cubic.zone"

# Completions can legitimately run for minutes (the server's own execution
# deadline is ~150s), so the read timeout is generous.
DEFAULT_TIMEOUT = httpx.Timeout(180.0, connect=5.0)
DEFAULT_MAX_RETRIES = 2

# Statuses safe to retry only when the request carries an idempotency key
# (client_request_id): the server may or may not have started executing.
_RETRY_STATUSES_IDEMPOTENT = {500, 502, 503}


def classify_retry(
    response: httpx.Response, exc: err.CubicError, idempotent: bool
) -> tuple[bool, float | None]:
    """Decide whether a failed response may be retried, and any server-directed delay."""
    status = response.status_code
    if status == 429:
        # Capacity load-shedding has no error_code and is always safe to
        # retry (the request was never admitted). Credit/quota 429s are
        # caller errors — never retry those.
        if isinstance(exc, err.RateLimitError) and exc.error_code is None:
            return True, exc.retry_after
        return False, None
    if status in _RETRY_STATUSES_IDEMPOTENT and idempotent:
        return True, None
    return False, None


def next_delay(attempt: int, retry_after: float | None, base: float) -> float:
    if retry_after is not None:
        return retry_after
    delay = min(base * (2 ** (attempt - 1)), 8.0)
    return delay + random.uniform(0, delay / 4)


class Cubic:
    """Synchronous client for the Cubic API.

    Args:
        api_key: A ``mxk_…`` API key. Falls back to the ``CUBIC_API_KEY``
            environment variable.
        base_url: API origin. Falls back to ``CUBIC_BASE_URL``, then the
            hosted API (https://api.cubic.zone).
        timeout: httpx timeout for all requests.
        max_retries: Automatic retries for transient failures (connection
            errors, capacity 429s, and — when the request is idempotent —
            5xx responses).
        http_client: Bring your own ``httpx.Client`` (proxies, custom
            transports, testing). The SDK will not close it for you.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: httpx.Timeout | float | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.Client | None = None,
        backoff_base: float = 0.5,
    ) -> None:
        self.api_key = api_key or os.environ.get("CUBIC_API_KEY")
        if not self.api_key:
            raise err.CubicError(
                "No API key provided. Pass api_key=... or set the CUBIC_API_KEY "
                "environment variable. Keys are created in the Cubic dashboard "
                "and start with 'mxk_'."
            )
        self.base_url = (base_url or os.environ.get("CUBIC_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.max_retries = max_retries
        self._backoff_base = backoff_base
        self._own_http = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout if timeout is not None else DEFAULT_TIMEOUT)
        # Remembers which public IDs turned out to be polycubes, so we stop
        # attaching fields the chain path rejects (e.g. client_request_id).
        self._kind_cache: dict[str, str] = {}

        from .resources.completions import Completions
        from .resources.cubes import Cubes
        from .resources.models import Models

        self.completions = Completions(self)
        self.cubes = Cubes(self)
        self.models = Models(self)

    # ---- lifecycle ----
    def close(self) -> None:
        if self._own_http:
            self._http.close()

    def __enter__(self) -> "Cubic":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- transport ----
    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        idempotent: bool = False,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": f"cubic-python/{__version__}",
        }
        attempt = 0
        while True:
            retry_after: float | None = None
            try:
                response = self._http.request(method, url, json=json_body, params=params, headers=headers)
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                # The request never reached the server — always safe to retry.
                exc: err.CubicError = err.APIConnectionError(f"Could not reach {self.base_url}: {e}")
                retryable = True
            except httpx.TimeoutException as e:
                exc = err.APITimeoutError(f"Request timed out: {e}")
                retryable = idempotent
            except httpx.TransportError as e:
                exc = err.APIConnectionError(f"Transport error: {e}")
                retryable = idempotent
            else:
                if response.status_code < 400:
                    return response
                exc = error_from_response(response)
                retryable, retry_after = classify_retry(response, exc, idempotent)

            if not retryable or attempt >= self.max_retries:
                raise exc
            attempt += 1
            delay = next_delay(attempt, retry_after, self._backoff_base)
            if delay > 0:
                time.sleep(delay)


def _flatten_validation_detail(detail: list[Any]) -> str:
    """Flatten FastAPI's 422 detail array into one readable message."""
    parts = []
    for item in detail:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        loc = item.get("loc") or []
        # drop the leading "body" segment — callers think in field names
        field = ".".join(str(x) for x in loc[1:] if x != "__root__") or ".".join(map(str, loc))
        msg = item.get("msg", "invalid")
        parts.append(f"{field}: {msg}" if field else msg)
    return "; ".join(parts) or "Invalid request"


def error_from_response(response: httpx.Response) -> err.CubicError:
    """Map a non-2xx API response onto the SDK exception hierarchy.

    Dispatch is by ``error_code`` first, HTTP status second — the API surfaces
    e.g. insufficient credits under more than one status.
    """
    status = response.status_code
    request_id = response.headers.get("X-Request-ID")
    try:
        body = response.json()
    except Exception:
        body = None
    detail = body.get("detail") if isinstance(body, dict) else None
    error_code = body.get("error_code") if isinstance(body, dict) else None
    common: dict[str, Any] = {
        "error_code": error_code,
        "status_code": status,
        "request_id": request_id,
        "body": body,
    }

    # Pydantic validation errors arrive as a list of field errors.
    if isinstance(detail, list):
        return err.InvalidRequestError(_flatten_validation_detail(detail), **common)

    message = detail if isinstance(detail, str) else f"HTTP {status} from Cubic API"

    if error_code == "insufficient_credits" or status == 402:
        g = body if isinstance(body, dict) else {}
        return err.InsufficientCreditsError(
            message,
            required=g.get("required"),
            balance=g.get("balance"),
            grace=g.get("grace"),
            topup_allowed=g.get("topup_allowed"),
            **common,
        )
    if status == 401:
        return err.AuthenticationError(message, **common)
    if status == 403:
        return err.PermissionDeniedError(message, **common)
    if status == 404:
        if error_code == "version_not_found":
            return err.VersionNotFoundError(message, **common)
        if error_code in ("cube_not_found", "chain_not_found", "prompt_not_found"):
            return err.CubeNotFoundError(
                message + " (the ID may not exist, or your key may not own it — "
                "marketplace cube definitions are not readable by subscribers)",
                **common,
            )
        if error_code == "completion_not_found":
            return err.CompletionNotFoundError(message, **common)
        return err.NotFoundError(message, **common)
    if status == 422:
        return err.InvalidRequestError(message, **common)
    if status == 429:
        retry_after_header = response.headers.get("Retry-After")
        try:
            retry_after = float(retry_after_header) if retry_after_header else None
        except ValueError:
            retry_after = None
        return err.RateLimitError(message, retry_after=retry_after, **common)
    if status == 504:
        # The deadline response is a CompletionResponse envelope, not the
        # standard error shape — pull the human message out of attempt_errors.
        if isinstance(body, dict):
            for ae in body.get("attempt_errors") or []:
                if isinstance(ae, dict) and ae.get("message"):
                    message = ae["message"]
                    common["error_code"] = ae.get("error_code")
                    break
        return err.CompletionTimeoutError(message, **common)
    if status >= 500:
        return err.InternalServerError(message, **common)
    return err.CubicError(message, **common)
