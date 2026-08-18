"""Datasets and evals — the surface a CI job drives.

The reason this exists: **evals nobody enforces get ignored within a month**.
Running an eval from a dashboard proves it works once; running it from CI, and
failing the build when it regresses, is what keeps it true.

``evals.run(..., wait=True)`` is the whole ergonomic story — a dataset run is
queued on a worker, so the SDK polls it to a terminal state and hands back a
result whose ``passed`` says plainly whether to fail the build.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from ..types import Dataset, DatasetRow, Eval, EvalCaseResult, EvalQuote, EvalRun

if TYPE_CHECKING:
    from .._async_client import AsyncCubic
    from .._client import Cubic

# A batch of a few hundred cases takes minutes, not seconds.
DEFAULT_WAIT_TIMEOUT = 1800.0
POLL_INTERVAL = 2.0
TERMINAL = ("done", "cancelled")


def _rows_payload(rows: list[dict[str, Any]]) -> dict:
    return {"rows": rows}


class Datasets:
    """Named sets of input cases. Address them by their public ``dset…`` id."""

    def __init__(self, client: "Cubic") -> None:
        self._client = client

    def list(self, *, cube_id: str | None = None) -> list[Dataset]:
        path = "/v1/datasets" + (f"?cube_id={cube_id}" if cube_id else "")
        response = self._client.request("GET", path, idempotent=True)
        return [Dataset.model_validate(d) for d in response.json()]

    def create(
        self,
        name: str,
        *,
        description: str | None = None,
        project_id: str | None = None,
        cube_id: str | None = None,
    ) -> Dataset:
        """Create an empty dataset. ``cube_id`` is an affinity, not a binding —
        any dataset can be run against any Cube."""
        body = {
            "name": name,
            "description": description,
            "project_id": project_id,
            "cube_id": cube_id,
        }
        response = self._client.request("POST", "/v1/datasets", json_body=body)
        return Dataset.model_validate(response.json())

    def retrieve(self, dataset_id: str) -> Dataset:
        response = self._client.request("GET", f"/v1/datasets/{dataset_id}", idempotent=True)
        return Dataset.model_validate(response.json())

    def delete(self, dataset_id: str) -> None:
        self._client.request("DELETE", f"/v1/datasets/{dataset_id}")

    def rows(self, dataset_id: str, *, limit: int = 100, offset: int = 0) -> list[DatasetRow]:
        response = self._client.request(
            "GET", f"/v1/datasets/{dataset_id}/rows?limit={limit}&offset={offset}", idempotent=True
        )
        return [DatasetRow.model_validate(r) for r in response.json()["items"]]

    def add_rows(self, dataset_id: str, rows: list[dict[str, Any]]) -> int:
        """Append rows. Each is ``{"variables": {...}, "expected_output": ...}``.
        Returns how many landed."""
        response = self._client.request(
            "POST", f"/v1/datasets/{dataset_id}/rows/bulk", json_body=_rows_payload(rows)
        )
        return response.json()["added"]

    def import_csv(
        self,
        dataset_id: str,
        csv_text: str,
        *,
        mapping: dict[str, str] | None = None,
        expected_output_column: str | None = None,
        has_header: bool = True,
        dry_run: bool = False,
    ) -> dict:
        """Import rows from CSV under an explicit column → variable mapping.

        ``dry_run=True`` runs the same parse the commit will run and reports what
        it would create, without writing — so a preview cannot drift from the
        import it previews.
        """
        body = {
            "csv_text": csv_text,
            "has_header": has_header,
            "mapping": mapping or {},
            "expected_output_column": expected_output_column,
            "dry_run": dry_run,
        }
        response = self._client.request("POST", f"/v1/datasets/{dataset_id}/import", json_body=body)
        return response.json()

    def export_csv(self, dataset_id: str) -> str:
        response = self._client.request(
            "GET", f"/v1/datasets/{dataset_id}/export", idempotent=True
        )
        return response.text


class Evals:
    """Saved, graded checks. Address them by ``eval…`` id or internal UUID."""

    def __init__(self, client: "Cubic") -> None:
        self._client = client

    def list(self) -> list[Eval]:
        response = self._client.request("GET", "/v1/evals", idempotent=True)
        return [Eval.model_validate(e) for e in response.json()]

    def retrieve(self, eval_id: str) -> Eval:
        response = self._client.request("GET", f"/v1/evals/{eval_id}", idempotent=True)
        return Eval.model_validate(response.json())

    def quote(self, eval_id: str) -> EvalQuote:
        """What a run would cost, and against how many cases. Worth checking
        before a scheduled job spends four figures of credits."""
        response = self._client.request("GET", f"/v1/evals/{eval_id}/quote", idempotent=True)
        return EvalQuote.model_validate(response.json())

    def set_dataset(self, eval_id: str, dataset_id: str | None) -> Eval:
        """Point the eval at a dataset, or ``None`` for its own inline inputs."""
        response = self._client.request(
            "PUT", f"/v1/evals/{eval_id}/dataset", json_body={"dataset_id": dataset_id}
        )
        return Eval.model_validate(response.json())

    def run(
        self,
        eval_id: str,
        *,
        wait: bool = False,
        timeout: float = DEFAULT_WAIT_TIMEOUT,
        poll_interval: float = POLL_INTERVAL,
    ) -> EvalRun:
        """Run the eval.

        An inline eval returns its verdict directly. A dataset-backed one is
        queued on a worker: with ``wait=True`` this polls until it finishes and
        returns the completed run, which is what a CI step wants.

        Raises ``TimeoutError`` if it hasn't finished inside ``timeout`` — the
        run keeps going server-side, and ``retrieve_run`` will still find it.
        """
        response = self._client.request("POST", f"/v1/evals/{eval_id}/run")
        run = EvalRun.model_validate(response.json())
        if not wait or run.run_state in TERMINAL:
            return run

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            run = self.retrieve_run(run.id)
            if run.run_state in TERMINAL:
                return run
        raise TimeoutError(
            f"Eval run {run.id} did not finish within {timeout:.0f}s "
            f"({run.case_passed + run.case_failed + run.case_error}/{run.case_total} cases graded)."
        )

    def retrieve_run(self, run_id: str) -> EvalRun:
        response = self._client.request("GET", f"/v1/evals/runs/{run_id}", idempotent=True)
        return EvalRun.model_validate(response.json())

    def cases(
        self, run_id: str, *, verdict: str | None = None, limit: int = 100
    ) -> list[EvalCaseResult]:
        """A run's per-case results. ``verdict="fail"`` is what makes a
        200-case run readable — and what a CI log should print."""
        path = f"/v1/evals/runs/{run_id}/cases?limit={limit}"
        if verdict:
            path += f"&verdict={verdict}"
        response = self._client.request("GET", path, idempotent=True)
        return [EvalCaseResult.model_validate(c) for c in response.json()["items"]]

    def compare(self, eval_id: str, a: str, b: str, *, changed_only: bool = True) -> dict:
        """Diff two runs case by case. Averages hide regressions; this doesn't."""
        response = self._client.request(
            "GET",
            f"/v1/evals/{eval_id}/compare?a={a}&b={b}&changed_only={str(changed_only).lower()}",
            idempotent=True,
        )
        return response.json()


class AsyncDatasets(Datasets):
    """Async mirror. Every method is awaited; the paths are identical."""

    def __init__(self, client: "AsyncCubic") -> None:  # noqa: D107
        self._client = client

    async def list(self, *, cube_id: str | None = None) -> list[Dataset]:  # type: ignore[override]
        path = "/v1/datasets" + (f"?cube_id={cube_id}" if cube_id else "")
        response = await self._client.request("GET", path, idempotent=True)
        return [Dataset.model_validate(d) for d in response.json()]

    async def create(  # type: ignore[override]
        self,
        name: str,
        *,
        description: str | None = None,
        project_id: str | None = None,
        cube_id: str | None = None,
    ) -> Dataset:
        body = {
            "name": name,
            "description": description,
            "project_id": project_id,
            "cube_id": cube_id,
        }
        response = await self._client.request("POST", "/v1/datasets", json_body=body)
        return Dataset.model_validate(response.json())

    async def retrieve(self, dataset_id: str) -> Dataset:  # type: ignore[override]
        response = await self._client.request(
            "GET", f"/v1/datasets/{dataset_id}", idempotent=True
        )
        return Dataset.model_validate(response.json())

    async def delete(self, dataset_id: str) -> None:  # type: ignore[override]
        await self._client.request("DELETE", f"/v1/datasets/{dataset_id}")

    async def rows(  # type: ignore[override]
        self, dataset_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[DatasetRow]:
        response = await self._client.request(
            "GET", f"/v1/datasets/{dataset_id}/rows?limit={limit}&offset={offset}", idempotent=True
        )
        return [DatasetRow.model_validate(r) for r in response.json()["items"]]

    async def add_rows(  # type: ignore[override]
        self, dataset_id: str, rows: list[dict[str, Any]]
    ) -> int:
        response = await self._client.request(
            "POST", f"/v1/datasets/{dataset_id}/rows/bulk", json_body=_rows_payload(rows)
        )
        return response.json()["added"]

    async def import_csv(  # type: ignore[override]
        self,
        dataset_id: str,
        csv_text: str,
        *,
        mapping: dict[str, str] | None = None,
        expected_output_column: str | None = None,
        has_header: bool = True,
        dry_run: bool = False,
    ) -> dict:
        body = {
            "csv_text": csv_text,
            "has_header": has_header,
            "mapping": mapping or {},
            "expected_output_column": expected_output_column,
            "dry_run": dry_run,
        }
        response = await self._client.request(
            "POST", f"/v1/datasets/{dataset_id}/import", json_body=body
        )
        return response.json()

    async def export_csv(self, dataset_id: str) -> str:  # type: ignore[override]
        response = await self._client.request(
            "GET", f"/v1/datasets/{dataset_id}/export", idempotent=True
        )
        return response.text


class AsyncEvals(Evals):
    """Async mirror of :class:`Evals`."""

    def __init__(self, client: "AsyncCubic") -> None:  # noqa: D107
        self._client = client

    async def list(self) -> list[Eval]:  # type: ignore[override]
        response = await self._client.request("GET", "/v1/evals", idempotent=True)
        return [Eval.model_validate(e) for e in response.json()]

    async def retrieve(self, eval_id: str) -> Eval:  # type: ignore[override]
        response = await self._client.request("GET", f"/v1/evals/{eval_id}", idempotent=True)
        return Eval.model_validate(response.json())

    async def quote(self, eval_id: str) -> EvalQuote:  # type: ignore[override]
        response = await self._client.request(
            "GET", f"/v1/evals/{eval_id}/quote", idempotent=True
        )
        return EvalQuote.model_validate(response.json())

    async def set_dataset(self, eval_id: str, dataset_id: str | None) -> Eval:  # type: ignore[override]
        response = await self._client.request(
            "PUT", f"/v1/evals/{eval_id}/dataset", json_body={"dataset_id": dataset_id}
        )
        return Eval.model_validate(response.json())

    async def run(  # type: ignore[override]
        self,
        eval_id: str,
        *,
        wait: bool = False,
        timeout: float = DEFAULT_WAIT_TIMEOUT,
        poll_interval: float = POLL_INTERVAL,
    ) -> EvalRun:
        import asyncio

        response = await self._client.request("POST", f"/v1/evals/{eval_id}/run")
        run = EvalRun.model_validate(response.json())
        if not wait or run.run_state in TERMINAL:
            return run

        loop_deadline = time.monotonic() + timeout
        while time.monotonic() < loop_deadline:
            await asyncio.sleep(poll_interval)
            run = await self.retrieve_run(run.id)
            if run.run_state in TERMINAL:
                return run
        raise TimeoutError(f"Eval run {run.id} did not finish within {timeout:.0f}s.")

    async def retrieve_run(self, run_id: str) -> EvalRun:  # type: ignore[override]
        response = await self._client.request(
            "GET", f"/v1/evals/runs/{run_id}", idempotent=True
        )
        return EvalRun.model_validate(response.json())

    async def cases(  # type: ignore[override]
        self, run_id: str, *, verdict: str | None = None, limit: int = 100
    ) -> list[EvalCaseResult]:
        path = f"/v1/evals/runs/{run_id}/cases?limit={limit}"
        if verdict:
            path += f"&verdict={verdict}"
        response = await self._client.request("GET", path, idempotent=True)
        return [EvalCaseResult.model_validate(c) for c in response.json()["items"]]

    async def compare(  # type: ignore[override]
        self, eval_id: str, a: str, b: str, *, changed_only: bool = True
    ) -> dict:
        response = await self._client.request(
            "GET",
            f"/v1/evals/{eval_id}/compare?a={a}&b={b}&changed_only={str(changed_only).lower()}",
            idempotent=True,
        )
        return response.json()
