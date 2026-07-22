from __future__ import annotations

import json

import httpx

import cubic
from conftest import error_envelope, make_async_client, make_client

POLYCUBE_BODY = {
    "polycube_id": "cbe_polygraph0001",
    "title": "Draft-and-polish",
    "description": None,
    "callback_url": None,
    "created_at": "2026-07-22T10:00:00Z",
    "updated_at": "2026-07-22T10:00:00Z",
    "nodes": [
        {
            "node_key": "draft",
            "cube_id": "cbe_draftcube0001",
            "title": "Drafter",
            "completion_type": "fallback",
            "version_number": None,
            "resolved_version": 3,
            "version_label": "1.2.0",
            "variables": {"topic": {"type": "string", "required": True}},
        },
        {
            "node_key": "polish",
            "cube_id": "cbe_polishcube001",
            "title": "Polisher",
            "completion_type": "fallback",
            "version_number": 2,
            "resolved_version": 2,
            "version_label": "1.1.0",
            "variables": {"draft": {"type": "string", "required": True}},
        },
    ],
    "edges": [
        {
            "source_node_key": "draft",
            "target_node_key": "polish",
            "source_field": None,
            "target_variable": "draft",
        }
    ],
    "inputs": [{"name": "topic", "type": "string", "required": True, "consumers": []}],
    "warnings": [],
}


def test_create_translates_sdk_node_shape_and_sends_idempotency_key():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/v1/polycubes"
        seen["idem"] = request.headers.get("Idempotency-Key")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(201, json=POLYCUBE_BODY)

    with make_client(handler) as client:
        poly = client.polycubes.create(
            "Draft-and-polish",
            nodes=[
                {"node_key": "draft", "cube_id": "cbe_draftcube0001"},
                {"node_key": "polish", "cube_id": "cbe_polishcube001", "version": 2},
            ],
            edges=[
                {
                    "source_node_key": "draft",
                    "target_node_key": "polish",
                    "target_variable": "draft",
                }
            ],
        )

    assert poly.polycube_id == "cbe_polygraph0001"
    assert seen["idem"]  # auto-generated
    # SDK-flavored keys are translated to the wire contract.
    assert seen["body"]["nodes"][0] == {"node_key": "draft", "prompt_id": "cbe_draftcube0001"}
    assert seen["body"]["nodes"][1]["version_number"] == 2
    assert "cube_id" not in seen["body"]["nodes"][1] and "version" not in seen["body"]["nodes"][1]
    # Parsed graph carries the derived input signature.
    assert [i.name for i in poly.inputs] == ["topic"]
    assert poly.nodes[1].version_number == 2 and poly.nodes[0].version_number is None


def test_create_cycle_error_is_typed():
    def handler(request: httpx.Request) -> httpx.Response:
        return error_envelope(422, "Chain graph is invalid: … cycle …", "chain_graph_cycle")

    with make_client(handler) as client:
        try:
            client.polycubes.create(
                "Bad",
                nodes=[{"node_key": "a", "cube_id": "cbe_x00000000000"}],
                edges=[],
            )
            raise AssertionError("expected InvalidRequestError")
        except cubic.InvalidRequestError as e:
            assert e.error_code == "chain_graph_cycle"


def test_retrieve_and_update_graph():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=POLYCUBE_BODY)
        assert request.method == "PATCH"
        body = json.loads(request.content.decode())
        assert body["title"] == "Renamed"
        assert "nodes" not in body  # metadata-only update sends no graph
        return httpx.Response(200, json={**POLYCUBE_BODY, "title": "Renamed"})

    with make_client(handler) as client:
        poly = client.polycubes.retrieve("cbe_polygraph0001")
        assert poly.edges[0].target_variable == "draft"
        renamed = client.polycubes.update("cbe_polygraph0001", title="Renamed")
        assert renamed.title == "Renamed"


def test_cubes_update_can_move_project():
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content.decode()) == {"project_id": "prj_target0000001"}
        return httpx.Response(200, json={
            "cube_id": "cbe_plaincube0001", "title": "T", "completion_type": "fallback",
            "current_version": 1, "version_number": 1, "user_prompt": "x",
        })

    with make_client(handler) as client:
        cube = client.cubes.update("cbe_plaincube0001", project_id="prj_target0000001")
    assert cube.cube_id == "cbe_plaincube0001"


async def test_async_polycube_create():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=POLYCUBE_BODY)

    async with make_async_client(handler) as client:
        poly = await client.polycubes.create(
            "Draft-and-polish",
            nodes=[{"node_key": "draft", "cube_id": "cbe_draftcube0001"}],
        )
    assert poly.title == "Draft-and-polish"
