from __future__ import annotations

import httpx

from conftest import make_async_client, make_client

PROJECTS_BODY = [
    {"project_id": "prj_default0000001", "name": "Default", "description": "Your default project"},
    {
        "project_id": "prj_market00000001",
        "name": "Marketplace",
        "description": "Cubes you've subscribed to from the marketplace.",
        "is_marketplace": True,
    },
]


def test_list_returns_public_ids_only():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path == "/v1/projects"
        return httpx.Response(200, json=PROJECTS_BODY)

    with make_client(handler) as client:
        projects = client.projects.list()

    assert [p.project_id for p in projects] == ["prj_default0000001", "prj_market00000001"]
    assert projects[0].is_marketplace is False  # tolerant default
    assert projects[1].is_marketplace is True


async def test_async_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=PROJECTS_BODY)

    async with make_async_client(handler) as client:
        projects = await client.projects.list()
    assert projects[0].name == "Default"
