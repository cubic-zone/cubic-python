"""The projects resource: list your projects (placement targets for cubes)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..types import Project

if TYPE_CHECKING:
    from .._async_client import AsyncCubic
    from .._client import Cubic

LIST_DOC = """List your projects — the placement targets for ``cubes.create``.

        Returns public ``prj_…`` ids only. Your default project sorts first and
        the auto-provisioned Marketplace project last (``is_marketplace=True``;
        it holds marketplace subscriptions, not your own cubes).
        """


class Projects:
    def __init__(self, client: "Cubic") -> None:
        self._client = client

    def list(self) -> list[Project]:
        response = self._client.request("GET", "/v1/projects", idempotent=True)
        return [Project.model_validate(p) for p in response.json()]

    list.__doc__ = LIST_DOC


class AsyncProjects:
    def __init__(self, client: "AsyncCubic") -> None:
        self._client = client

    async def list(self) -> list[Project]:
        response = await self._client.request("GET", "/v1/projects", idempotent=True)
        return [Project.model_validate(p) for p in response.json()]

    list.__doc__ = LIST_DOC
