"""The models resource: the public model catalog, cached in-process.

The catalog backs the ``models=`` override on ``completions.create`` and cube
model stacks. It changes at most ~daily (a sync job refreshes it server-side),
so ``list()`` caches the response for an hour. Lookups are explicit — the SDK
never auto-validates your overrides against the cache; the server stays the
source of truth.
"""

from __future__ import annotations

import difflib
import time
from typing import TYPE_CHECKING

from .. import _exceptions as err
from ..types import Model

if TYPE_CHECKING:
    from .._async_client import AsyncCubic
    from .._client import Cubic

CACHE_TTL_SECONDS = 3600.0

LIST_DOC = """Return the model catalog, optionally filtered by ``provider``.

        The full catalog is fetched once and cached in-process for an hour
        (it changes at most ~daily server-side); ``force_refresh=True``
        bypasses the cache.
        """

RETRIEVE_DOC = """Look up one catalog model by its ``model_name`` call-string.

        Served from the cached catalog — no extra HTTP call after the first.
        The same name can exist under more than one provider (e.g. natively
        and via openrouter); pass ``provider=`` to disambiguate. Raises
        :class:`~cubic.ModelNotFoundError` (with close-match suggestions)
        when nothing matches.
        """


def _filter(catalog: list[Model], provider: str | None) -> list[Model]:
    if provider is None:
        return list(catalog)
    return [m for m in catalog if m.provider == provider]


def _lookup(catalog: list[Model], model_name: str, provider: str | None) -> Model:
    matches = [m for m in _filter(catalog, provider) if m.model_name == model_name]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        providers = ", ".join(sorted(m.provider for m in matches))
        raise err.CubicError(
            f"Model '{model_name}' exists under multiple providers ({providers}) — "
            "pass provider= to disambiguate"
        )
    candidates = {m.model_name for m in _filter(catalog, provider)}
    suggestions = difflib.get_close_matches(model_name, candidates, n=3)
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    scope = f" for provider '{provider}'" if provider else ""
    raise err.ModelNotFoundError(f"Model '{model_name}' is not in the catalog{scope}.{hint}")


class Models:
    def __init__(self, client: "Cubic") -> None:
        self._client = client
        self._cache: list[Model] | None = None
        self._cached_at = 0.0
        self._cache_ttl = CACHE_TTL_SECONDS

    def list(self, *, provider: str | None = None, force_refresh: bool = False) -> list[Model]:
        if (
            force_refresh
            or self._cache is None
            or time.monotonic() - self._cached_at > self._cache_ttl
        ):
            response = self._client.request("GET", "/v1/models", idempotent=True)
            self._cache = [Model.model_validate(m) for m in response.json()]
            self._cached_at = time.monotonic()
        return _filter(self._cache, provider)

    list.__doc__ = LIST_DOC

    def retrieve(self, model_name: str, *, provider: str | None = None) -> Model:
        return _lookup(self.list(), model_name, provider)

    retrieve.__doc__ = RETRIEVE_DOC


class AsyncModels:
    def __init__(self, client: "AsyncCubic") -> None:
        self._client = client
        self._cache: list[Model] | None = None
        self._cached_at = 0.0
        self._cache_ttl = CACHE_TTL_SECONDS

    async def list(self, *, provider: str | None = None, force_refresh: bool = False) -> list[Model]:
        if (
            force_refresh
            or self._cache is None
            or time.monotonic() - self._cached_at > self._cache_ttl
        ):
            response = await self._client.request("GET", "/v1/models", idempotent=True)
            self._cache = [Model.model_validate(m) for m in response.json()]
            self._cached_at = time.monotonic()
        return _filter(self._cache, provider)

    list.__doc__ = LIST_DOC

    async def retrieve(self, model_name: str, *, provider: str | None = None) -> Model:
        return _lookup(await self.list(), model_name, provider)

    retrieve.__doc__ = RETRIEVE_DOC
