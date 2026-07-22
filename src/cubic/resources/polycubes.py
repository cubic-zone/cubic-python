"""The polycubes resource: author chains of cubes (owner-only).

A polycube is a DAG whose nodes are your cubes and whose edges map one node's
output onto a downstream node's variable. It has no versions — the graph IS the
definition — and runs through ``completions.create`` like any cube id.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from ..types import Polycube

if TYPE_CHECKING:
    from .._async_client import AsyncCubic
    from .._client import Cubic

CREATE_DOC = """Create a polycube — a DAG of your cubes — in one call.

        ``nodes`` entries are ``{"node_key": ..., "cube_id": ..., "version": n?}``
        (``version`` omitted = the node follows the cube's current version);
        ``edges`` entries are ``{"source_node_key": ..., "target_node_key": ...,
        "target_variable": ..., "source_field": ...?}`` — ``source_field`` picks
        a response-format field, omitted = the node's whole text output.

        Every node's cube must be placed in the polycube's project and use the
        fallback strategy. The graph must be acyclic. The returned
        :class:`Polycube` carries the derived ``inputs`` signature — what a
        ``completions.create`` run must supply. Creation is idempotent (an
        ``Idempotency-Key`` is auto-attached unless ``idempotency_key`` is given).
        """

RETRIEVE_DOC = """Fetch the polycube's definition: graph, derived input signature,
        and non-blocking drift warnings. Owner-only —
        :class:`~cubic.NotFoundError` for unknown or foreign ids.
        """

UPDATE_DOC = """Update metadata and/or replace the graph.

        ``nodes`` and ``edges`` must travel together (wholesale replace — a
        polycube has no versions). ``callback_url=""`` clears the callback;
        omitted fields stay untouched.
        """


def _node_payload(n: dict[str, Any]) -> dict[str, Any]:
    """Accept SDK-flavored node dicts (cube_id / version) and translate to the
    wire contract (prompt_id / version_number). Wire-shaped dicts pass through."""
    out = dict(n)
    if "cube_id" in out:
        out["prompt_id"] = out.pop("cube_id")
    if "version" in out:
        out["version_number"] = out.pop("version")
    return out


def build_polycube_payload(
    title: str | None,
    *,
    description: str | None,
    callback_url: str | None,
    nodes: list[dict[str, Any]] | None,
    edges: list[dict[str, Any]] | None,
    project_id: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description
    if callback_url is not None:
        payload["callback_url"] = callback_url
    if nodes is not None:
        payload["nodes"] = [_node_payload(n) for n in nodes]
    if edges is not None:
        payload["edges"] = edges
    if project_id is not None:
        payload["project_id"] = project_id
    return payload


class Polycubes:
    def __init__(self, client: "Cubic") -> None:
        self._client = client

    def create(
        self,
        title: str,
        *,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]] | None = None,
        description: str | None = None,
        callback_url: str | None = None,
        project_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Polycube:
        payload = build_polycube_payload(
            title,
            description=description,
            callback_url=callback_url,
            nodes=nodes,
            edges=edges or [],
            project_id=project_id,
        )
        response = self._client.request(
            "POST",
            "/v1/polycubes",
            json_body=payload,
            idempotent=True,  # safe: the Idempotency-Key makes retries replay
            extra_headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
        )
        return Polycube.model_validate(response.json())

    def retrieve(self, polycube_id: str) -> Polycube:
        response = self._client.request(
            "GET", f"/v1/polycubes/{polycube_id}", idempotent=True
        )
        return Polycube.model_validate(response.json())

    def update(
        self,
        polycube_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        callback_url: str | None = None,
        nodes: list[dict[str, Any]] | None = None,
        edges: list[dict[str, Any]] | None = None,
    ) -> Polycube:
        payload = build_polycube_payload(
            title,
            description=description,
            callback_url=callback_url,
            nodes=nodes,
            edges=edges,
            project_id=None,
        )
        response = self._client.request(
            "PATCH", f"/v1/polycubes/{polycube_id}", json_body=payload
        )
        return Polycube.model_validate(response.json())

    create.__doc__ = CREATE_DOC
    retrieve.__doc__ = RETRIEVE_DOC
    update.__doc__ = UPDATE_DOC


class AsyncPolycubes:
    def __init__(self, client: "AsyncCubic") -> None:
        self._client = client

    async def create(
        self,
        title: str,
        *,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]] | None = None,
        description: str | None = None,
        callback_url: str | None = None,
        project_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Polycube:
        payload = build_polycube_payload(
            title,
            description=description,
            callback_url=callback_url,
            nodes=nodes,
            edges=edges or [],
            project_id=project_id,
        )
        response = await self._client.request(
            "POST",
            "/v1/polycubes",
            json_body=payload,
            idempotent=True,
            extra_headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
        )
        return Polycube.model_validate(response.json())

    async def retrieve(self, polycube_id: str) -> Polycube:
        response = await self._client.request(
            "GET", f"/v1/polycubes/{polycube_id}", idempotent=True
        )
        return Polycube.model_validate(response.json())

    async def update(
        self,
        polycube_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        callback_url: str | None = None,
        nodes: list[dict[str, Any]] | None = None,
        edges: list[dict[str, Any]] | None = None,
    ) -> Polycube:
        payload = build_polycube_payload(
            title,
            description=description,
            callback_url=callback_url,
            nodes=nodes,
            edges=edges,
            project_id=None,
        )
        response = await self._client.request(
            "PATCH", f"/v1/polycubes/{polycube_id}", json_body=payload
        )
        return Polycube.model_validate(response.json())

    create.__doc__ = CREATE_DOC
    retrieve.__doc__ = RETRIEVE_DOC
    update.__doc__ = UPDATE_DOC
