"""The cubes resource: read cube definitions (owner-only)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..types import Cube

if TYPE_CHECKING:
    from .._async_client import AsyncCubic
    from .._client import Cubic

RETRIEVE_DOC = """Fetch a cube's full definition — system instructions, user prompt
        template, variables schema, model stack, parameters, response format.

        ``version`` pins a historical version; omit it for the current one.

        Only your own cubes are readable: unknown IDs, other users' cubes, and
        marketplace cubes you subscribe to all raise
        :class:`~cubic.CubeNotFoundError` (definitions are the seller's IP).
        Polycube definitions are not yet available on this endpoint.
        """


class Cubes:
    def __init__(self, client: "Cubic") -> None:
        self._client = client

    def retrieve(self, cube_id: str, *, version: int | None = None) -> Cube:
        params = {"version_number": version} if version is not None else None
        response = self._client.request(
            "GET", f"/v1/cubes/{cube_id}", params=params, idempotent=True
        )
        return Cube.model_validate(response.json())

    retrieve.__doc__ = RETRIEVE_DOC


class AsyncCubes:
    def __init__(self, client: "AsyncCubic") -> None:
        self._client = client

    async def retrieve(self, cube_id: str, *, version: int | None = None) -> Cube:
        params = {"version_number": version} if version is not None else None
        response = await self._client.request(
            "GET", f"/v1/cubes/{cube_id}", params=params, idempotent=True
        )
        return Cube.model_validate(response.json())

    retrieve.__doc__ = RETRIEVE_DOC
