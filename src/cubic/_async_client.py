"""The asynchronous Cubic client — same surface as :class:`cubic.Cubic`, awaitable."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from . import _exceptions as err
from ._client import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    classify_retry,
    error_from_response,
    next_delay,
)
from ._version import __version__


class AsyncCubic:
    """Asynchronous client for the Cubic API.

    Accepts the same arguments as :class:`cubic.Cubic`; every resource method
    is a coroutine. Use it as an async context manager or call ``aclose()``.

    Args:
        api_key: A ``mxk_…`` API key. Falls back to the ``CUBIC_API_KEY``
            environment variable.
        base_url: API origin. Falls back to ``CUBIC_BASE_URL``, then the local
            dev server.
        timeout: httpx timeout for all requests.
        max_retries: Automatic retries for transient failures (connection
            errors, capacity 429s, and — when the request is idempotent —
            5xx responses).
        http_client: Bring your own ``httpx.AsyncClient`` (proxies, custom
            transports, testing). The SDK will not close it for you.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: httpx.Timeout | float | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.AsyncClient | None = None,
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
        self._http = http_client or httpx.AsyncClient(
            timeout=timeout if timeout is not None else DEFAULT_TIMEOUT
        )
        self._kind_cache: dict[str, str] = {}

        from .resources.completions import AsyncCompletions
        from .resources.cubes import AsyncCubes
        from .resources.models import AsyncModels

        self.completions = AsyncCompletions(self)
        self.cubes = AsyncCubes(self)
        self.models = AsyncModels(self)

    # ---- lifecycle ----
    async def aclose(self) -> None:
        if self._own_http:
            await self._http.aclose()

    async def __aenter__(self) -> "AsyncCubic":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # ---- transport ----
    async def request(
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
                response = await self._http.request(
                    method, url, json=json_body, params=params, headers=headers
                )
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
                await asyncio.sleep(delay)
