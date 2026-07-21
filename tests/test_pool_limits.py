"""Constructor pool-limit kwargs forward to the SDK-owned httpx transport."""

from __future__ import annotations

import httpx
import pytest

import cubic
from cubic import AsyncCubic, Cubic
from cubic._client import pool_limit_kwargs


def test_no_kwargs_means_httpx_defaults():
    assert pool_limit_kwargs(None, None) == {}


def test_partial_kwargs_fill_httpx_defaults():
    limits = pool_limit_kwargs(200, None)["limits"]
    assert limits.max_connections == 200
    assert limits.max_keepalive_connections == 20
    limits = pool_limit_kwargs(None, 50)["limits"]
    assert limits.max_connections == 100
    assert limits.max_keepalive_connections == 50


@pytest.mark.parametrize("cls", [Cubic, AsyncCubic])
def test_limits_reach_the_transport(monkeypatch, cls):
    captured = {}
    target = httpx.Client if cls is Cubic else httpx.AsyncClient
    real_init = target.__init__

    def spy(self, *args, **kwargs):
        captured.update(kwargs)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(target, "__init__", spy)
    cls(api_key="mxk_x", max_connections=64, max_keepalive_connections=8)
    assert captured["limits"].max_connections == 64
    assert captured["limits"].max_keepalive_connections == 8
    assert captured["limits"].keepalive_expiry == 5.0
    # the SDK's generous completion timeout still applies
    assert captured["timeout"].read == 180.0


@pytest.mark.parametrize("cls,httpx_cls", [(Cubic, httpx.Client), (AsyncCubic, httpx.AsyncClient)])
def test_conflict_with_byo_client_raises(cls, httpx_cls):
    own = httpx_cls()
    with pytest.raises(cubic.CubicError, match="http_client"):
        cls(api_key="mxk_x", http_client=own, max_connections=10)
