"""Datasets, evals, and the CI gate.

The exit code is the product here: an eval a pipeline can't fail on is a
dashboard decoration. Most of these tests are about what `cubic evals run`
returns to the shell.
"""

from __future__ import annotations

import httpx
import pytest

from conftest import body_of, make_client
from cubic._cli import main

EVAL_BODY = {
    "id": "11111111-1111-1111-1111-111111111111",
    "public_id": "evalAbC123XyZ0000",
    "name": "Golden set",
    "graders": [{"id": "g1", "kind": "json_schema"}],
    "current_revision": 1,
    "judge_mode": "platform",
    "dataset_id": "22222222-2222-2222-2222-222222222222",
    "last_verdict": "pass",
}


def run_body(**over) -> dict:
    base = {
        "id": "33333333-3333-3333-3333-333333333333",
        "eval_id": EVAL_BODY["id"],
        "run_state": "done",
        "verdict": "pass",
        "case_total": 3,
        "case_passed": 3,
        "case_failed": 0,
        "case_error": 0,
        "credits_charged": 3,
        "rationale": "3/3 cases passed",
    }
    base.update(over)
    return base


# ---- datasets ---------------------------------------------------------------
def test_creating_a_dataset_and_adding_rows(request_log):
    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        if request.url.path == "/v1/datasets" and request.method == "POST":
            return httpx.Response(201, json={"public_id": "dsetAAAA00000000", "name": "Cases", "row_count": 0})
        return httpx.Response(200, json={"added": 2, "skipped": [], "row_count": 2})

    client = make_client(handler)
    dataset = client.datasets.create("Cases", cube_id="cbe_x0000000000000")
    assert dataset.public_id == "dsetAAAA00000000"

    added = client.datasets.add_rows(
        dataset.public_id,
        [{"variables": {"name": "Ada"}}, {"variables": {"name": "Grace"}, "expected_output": "Hi"}],
    )
    assert added == 2
    assert body_of(request_log[-1])["rows"][1]["expected_output"] == "Hi"


def test_import_dry_run_is_the_same_call_as_the_commit(request_log):
    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(200, json={"columns": ["name"], "suggested_mapping": {"name": "name"},
                                         "parsed_rows": 2, "added": 0, "row_count": 0,
                                         "sample": [], "warnings": []})

    client = make_client(handler)
    client.datasets.import_csv("dsetAAAA00000000", "name\nAda\nGrace\n", dry_run=True)
    body = body_of(request_log[-1])
    assert body["dry_run"] is True and body["has_header"] is True


# ---- evals ------------------------------------------------------------------
def test_an_inline_run_needs_no_polling(request_log):
    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(200, json=run_body())

    client = make_client(handler)
    run = client.evals.run("evalAbC123XyZ0000", wait=True)
    assert run.passed is True
    # It came back done, so `wait` must not have cost an extra request.
    assert len(request_log) == 1


def test_a_batch_run_is_polled_to_completion(request_log):
    states = iter(["queued", "running", "done"])

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        if request.method == "POST":
            return httpx.Response(202, json=run_body(run_state="queued", verdict="error", case_passed=0))
        state = next(states)
        return httpx.Response(200, json=run_body(run_state=state, verdict="pass" if state == "done" else "error"))

    client = make_client(handler)
    run = client.evals.run("evalAbC123XyZ0000", wait=True, poll_interval=0.0)
    assert run.run_state == "done" and run.passed is True


def test_waiting_gives_up_rather_than_hanging_forever(request_log):
    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        status = 202 if request.method == "POST" else 200
        return httpx.Response(status, json=run_body(run_state="running", verdict="error", case_passed=1))

    client = make_client(handler)
    with pytest.raises(TimeoutError) as excinfo:
        client.evals.run("evalAbC123XyZ0000", wait=True, timeout=0.05, poll_interval=0.0)
    # The message says how far it got — "timed out" alone sends you to a browser.
    assert "cases graded" in str(excinfo.value)


def test_passed_is_false_while_a_run_is_still_going():
    from cubic.types import EvalRun

    # A queued run has decided nothing; only a finished, all-passing run passes.
    assert EvalRun.model_validate(run_body(run_state="running")).passed is False
    assert EvalRun.model_validate(run_body(verdict="fail", case_failed=1)).passed is False
    assert EvalRun.model_validate(run_body()).passed is True


# ---- the CI gate ------------------------------------------------------------
def _cli_client(monkeypatch, handler):
    """Point the CLI at a mock transport instead of the network."""
    client = make_client(handler)
    monkeypatch.setattr("cubic._cli.Cubic", lambda **kwargs: client)
    return client


def test_the_gate_exits_zero_when_the_eval_passes(monkeypatch, capsys):
    _cli_client(monkeypatch, lambda r: httpx.Response(200, json=run_body()))
    code = main(["--api-key", "mxk_x", "evals", "run", "evalAbC123XyZ0000", "--wait"])
    assert code == 0
    assert "PASS" in capsys.readouterr().out


def test_the_gate_exits_non_zero_on_a_regression(monkeypatch, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/cases" in request.url.path:
            return httpx.Response(200, json={"items": [
                {"id": "c1", "ordinal": 2, "variables": {"name": "Grace"},
                 "verdict": "fail", "rationale": "contains failed"}
            ], "total": 1, "limit": 100, "offset": 0})
        return httpx.Response(200, json=run_body(verdict="fail", case_passed=2, case_failed=1))

    _cli_client(monkeypatch, handler)
    code = main(["--api-key", "mxk_x", "evals", "run", "evalAbC123XyZ0000", "--wait"])
    assert code == 1
    out = capsys.readouterr()
    # The failing case is printed, so the log is the first place you look.
    assert "case 2" in out.err and "Grace" in out.err
    assert "contains failed" in out.err


def test_an_errored_run_fails_the_build_by_default(monkeypatch):
    """A run that couldn't be judged has told you nothing; treating silence as
    success is how a gate quietly stops gating."""
    _cli_client(monkeypatch, lambda r: httpx.Response(200, json=run_body(verdict="error", case_passed=0, case_error=3)))
    assert main(["--api-key", "mxk_x", "evals", "run", "evalAbC123XyZ0000", "--wait"]) == 1
    # …unless the caller explicitly opts out.
    assert main(
        ["--api-key", "mxk_x", "evals", "run", "evalAbC123XyZ0000", "--wait", "--fail-on", "fail"]
    ) == 0


def test_a_timeout_is_distinguishable_from_a_regression(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        status = 202 if request.method == "POST" else 200
        return httpx.Response(status, json=run_body(run_state="running", verdict="error"))

    _cli_client(monkeypatch, handler)
    # Exit 2, not 1: the run may still be perfectly fine, we just stopped waiting.
    assert main(
        ["--api-key", "mxk_x", "evals", "run", "evalAbC123XyZ0000", "--wait", "--timeout", "0.05"]
    ) == 2


def test_quote_prints_the_cost_without_running(monkeypatch, capsys):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json={"cases": 200, "legs": 1, "judge_graders": 1,
                                         "judge_mode": "platform", "credits": 800, "is_batch": True})

    _cli_client(monkeypatch, handler)
    assert main(["--api-key", "mxk_x", "evals", "run", "evalAbC123XyZ0000", "--quote"]) == 0
    assert '"credits": 800' in capsys.readouterr().out
    assert "POST" not in calls  # nothing was spent


def test_compare_fails_the_build_on_any_regression(monkeypatch, capsys):
    _cli_client(monkeypatch, lambda r: httpx.Response(200, json={
        "counts": {"regressed": 1, "fixed": 0, "unchanged": 9},
        "items": [{"status": "regressed", "ordinal": 4, "variables": {"name": "Ada"}}],
    }))
    code = main(["--api-key", "mxk_x", "evals", "compare", "evalAbC123XyZ0000", "--a", "r1", "--b", "r2"])
    assert code == 1
    assert "regressed 1" in capsys.readouterr().out


def test_an_edited_dataset_row_is_flagged_but_does_not_fail_the_build(monkeypatch, capsys):
    """A row edited between runs makes those verdicts incomparable.

    Silently folding it into "unchanged" would let a gate report on a diff it
    cannot read; failing the build on it would cry wolf over a dataset edit.
    """
    _cli_client(monkeypatch, lambda r: httpx.Response(200, json={
        "counts": {"regressed": 0, "fixed": 0, "unchanged": 9, "input_changed": 1},
        "items": [{"status": "input_changed", "ordinal": 2, "variables": {"name": "Grace"}}],
    }))
    code = main(["--api-key", "mxk_x", "evals", "compare", "evalAbC123XyZ0000", "--a", "r1", "--b", "r2"])
    assert code == 0
    out = capsys.readouterr().out
    assert "1 case(s) had their dataset row edited" in out
    assert "input_changed case 2" in out


def test_a_missing_api_key_is_an_error_not_a_pass(monkeypatch):
    monkeypatch.delenv("CUBIC_API_KEY", raising=False)
    # Exiting 0 here would let an unconfigured pipeline report green forever.
    assert main(["evals", "run", "evalAbC123XyZ0000"]) == 2
