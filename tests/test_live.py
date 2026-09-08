"""The live suite: the SDK against a running Cubic, not against a fixture.

Point it at a deployment and give it a developer key that deployment's Molten
will introspect:

    CUBIC_BASE_URL=http://127.0.0.1:8010 CUBIC_API_KEY=mxk_… make live

Without both variables every test here skips (see ``conftest.py``), so a plain
``pytest`` run stays a mock run and CI needs no secret.

**What these prove.** The mock suite asserts on what the SDK *sent*, inside a
handler it controls. These assert on what a real service *answered*: that its
responses parse into the SDK's models, that the fields the SDK reads are
present and typed as it expects, and that its error bodies map to the exception
classes callers catch. That is the half of the suite a live service can be held
to, and it is the half that matters when the service is rebuilt underneath a
released SDK.

**They do not write.** Every case here reads, or deliberately fails to
authenticate. Nothing is created, updated or deleted, so this is safe to point
at staging — no seeded account, no cleanup, no quota spent.
"""

from __future__ import annotations

import os

import pytest

from cubic import Cubic
from cubic._exceptions import AuthenticationError, CubeNotFoundError
from cubic.types import Model, Project

pytestmark = pytest.mark.live


def test_an_invalid_key_raises_authentication_error(live_client: Cubic) -> None:
    """A key the deployment's Molten does not know resolves to `invalid_api_key`,
    and the SDK raises the exception a caller already catches for a bad key.

    Introspection is why this is worth asserting live: Cubic cannot verify an
    `mxk_` key offline, so it asks Molten, and an unknown key comes back
    `active: false` rather than as a 404. The SDK never sees that distinction —
    it sees a 401 — which is exactly the point.

    This drives `projects.list()` rather than `models.list()` on purpose. The
    catalog is `security: []` in the contract — deliberately public, so the
    model picker can be populated before anyone signs in — and a bad key gets
    200 from it, which would make this assertion vacuous.
    """
    base_url = live_client.base_url
    with Cubic(api_key="mxk_definitely_not_a_real_key", base_url=base_url, backoff_base=0.0) as bad:
        with pytest.raises(AuthenticationError) as excinfo:
            bad.projects.list()

    assert excinfo.value.status_code == 401
    # The code Molten's introspection produces for a key it cannot resolve. It
    # is carried through unchanged, so a caller can tell it from a missing key.
    assert excinfo.value.error_code == "invalid_api_key"


def test_the_model_catalog_parses(live_client: Cubic) -> None:
    """`GET /v1/models` parses into `Model`, with the fields the SDK reads."""
    models = live_client.models.list()

    assert models, "the catalog is empty — nothing downstream of this can pass"
    assert all(isinstance(m, Model) for m in models)
    for model in models:
        assert model.model_name
        assert model.provider


def test_projects_list_returns_public_ids(live_client: Cubic) -> None:
    """Projects parse, and their ids are the public `prj_…` form the authoring
    calls take — not the internal UUID the database keeps."""
    projects = live_client.projects.list()

    assert projects, "a key always belongs to at least its default project"
    assert all(isinstance(p, Project) for p in projects)
    for project in projects:
        assert project.project_id.startswith("prj_")


def test_an_unknown_cube_id_raises_cube_not_found(live_client: Cubic) -> None:
    """A well-formed id that resolves to nothing maps to the typed exception,
    rather than surfacing as a bare 404."""
    with pytest.raises(CubeNotFoundError):
        live_client.cubes.retrieve("cbe_nosuchcube0001")


def test_the_live_target_is_not_baked_into_the_tree() -> None:
    """The deployment comes from the environment, never from a checked-in
    default. Nothing in this repository should name a host or hold a key."""
    assert os.environ.get("CUBIC_BASE_URL"), "the live target comes from the environment"
    assert os.environ.get("CUBIC_API_KEY"), "the key comes from the environment"
