"""models: catalog listing, in-process caching, and client-side lookup."""

from __future__ import annotations

import httpx
import pytest

import cubic

from conftest import make_async_client, make_client

CATALOG = [
    {
        "provider": "anthropic",
        "model_name": "claude-3-5-haiku",
        "owner": "Anthropic",
        "display_name": "Claude 3.5 Haiku",
        "context_window": 200000,
        "supports_reasoning": False,
        "supports_structured_output": True,
        "supports_temperature": True,
        "supports_web_search": True,
        "supports_tools": False,
        "input_per_1k": 0.0008,
        "output_per_1k": 0.004,
    },
    {
        "provider": "openai",
        "model_name": "gpt-4o-mini",
        "owner": "OpenAI",
        "display_name": "GPT-4o mini",
        "context_window": 128000,
        "supports_structured_output": True,
        "input_per_1k": 0.00015,
        "output_per_1k": 0.0006,
    },
    {
        "provider": "openrouter",
        "model_name": "gpt-4o-mini",
        "owner": "OpenAI",
        "display_name": "GPT-4o mini (OpenRouter)",
    },
]


def catalog_handler(counter: dict) -> callable:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        counter["n"] += 1
        return httpx.Response(200, json=CATALOG)

    return handler


def test_list_parses_and_caches():
    counter = {"n": 0}
    with make_client(catalog_handler(counter)) as client:
        models = client.models.list()
        assert len(models) == 3
        assert models[0].model_name == "claude-3-5-haiku"
        assert models[0].supports_web_search is True
        assert models[0].input_per_1k == 0.0008
        # second call served from cache
        client.models.list()
        assert counter["n"] == 1
        # force_refresh bypasses the cache
        client.models.list(force_refresh=True)
        assert counter["n"] == 2


def test_list_cache_expires():
    counter = {"n": 0}
    with make_client(catalog_handler(counter)) as client:
        client.models._cache_ttl = 0.0
        client.models.list()
        client.models.list()
    assert counter["n"] == 2


def test_list_provider_filter():
    counter = {"n": 0}
    with make_client(catalog_handler(counter)) as client:
        openai_models = client.models.list(provider="openai")
    assert [m.model_name for m in openai_models] == ["gpt-4o-mini"]


def test_retrieve_found():
    counter = {"n": 0}
    with make_client(catalog_handler(counter)) as client:
        model = client.models.retrieve("claude-3-5-haiku")
        assert model.provider == "anthropic"
        # served from the same cache — one HTTP call total
        client.models.retrieve("claude-3-5-haiku")
    assert counter["n"] == 1


def test_retrieve_ambiguous_requires_provider():
    counter = {"n": 0}
    with make_client(catalog_handler(counter)) as client:
        with pytest.raises(cubic.CubicError, match="multiple providers"):
            client.models.retrieve("gpt-4o-mini")
        model = client.models.retrieve("gpt-4o-mini", provider="openrouter")
    assert model.display_name == "GPT-4o mini (OpenRouter)"


def test_retrieve_unknown_suggests_close_matches():
    counter = {"n": 0}
    with make_client(catalog_handler(counter)) as client:
        with pytest.raises(cubic.ModelNotFoundError, match="Did you mean.*gpt-4o-mini"):
            client.models.retrieve("gpt-4oo-mini", provider="openai")


async def test_async_list_and_retrieve():
    counter = {"n": 0}
    async with make_async_client(catalog_handler(counter)) as client:
        models = await client.models.list()
        assert len(models) == 3
        model = await client.models.retrieve("claude-3-5-haiku")
        assert model.owner == "Anthropic"
    assert counter["n"] == 1
