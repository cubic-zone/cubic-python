"""``cubic`` — the command a CI job runs.

The point of this file is a single number: the **exit code**. An eval that a
pipeline can't fail on is a dashboard decoration, so the whole design is "run
the thing, print what broke, exit non-zero if it regressed".

    cubic evals run evalAbC123… --wait --fail-on fail,error

Reads ``CUBIC_API_KEY`` from the environment, like every other Cubic client.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import Cubic
from ._exceptions import CubicError

# What counts as "the build should fail". Errors are included by default: a run
# whose cases could not be judged has told you nothing, and treating silence as
# success is how a gate quietly stops gating.
DEFAULT_FAIL_ON = "fail,error"
EXIT_REGRESSED = 1
EXIT_ERROR = 2


def _print_failures(client: Cubic, run, limit: int) -> None:
    """Show what actually broke. A CI log that says "3 failed" and nothing else
    sends the reader back to a browser."""
    try:
        cases = client.evals.cases(run.id, verdict="fail", limit=limit)
    except CubicError:
        return
    for case in cases:
        variables = " ".join(f"{k}={v!r}" for k, v in (case.variables or {}).items())
        print(f"  ✗ case {case.ordinal}: {variables}", file=sys.stderr)
        if case.rationale:
            print(f"      {case.rationale}", file=sys.stderr)


def _run(args: argparse.Namespace) -> int:
    client = Cubic(api_key=args.api_key)
    fail_on = {v.strip() for v in args.fail_on.split(",") if v.strip()}

    if args.quote:
        quote = client.evals.quote(args.eval_id)
        print(json.dumps(quote.model_dump(), indent=2))
        return 0

    run = client.evals.run(args.eval_id, wait=args.wait, timeout=args.timeout)

    if run.run_state not in ("done", "cancelled"):
        # Started but not waited on: report where to look and succeed. The
        # caller explicitly chose not to block.
        print(f"Eval run {run.id} is {run.run_state} ({run.case_total} cases).")
        return 0

    graded = run.case_passed + run.case_failed + run.case_error
    print(
        f"{run.verdict.upper()} — {run.case_passed}/{graded} cases passed"
        + (f", {run.case_error} errored" if run.case_error else "")
        + f" ({run.credits_charged} credits)"
    )
    if run.case_failed:
        _print_failures(client, run, args.show)

    if run.verdict in fail_on:
        print(f"::error::Eval {args.eval_id} returned {run.verdict}", file=sys.stderr)
        return EXIT_REGRESSED
    return 0


def _compare(args: argparse.Namespace) -> int:
    client = Cubic(api_key=args.api_key)
    diff = client.evals.compare(args.eval_id, args.a, args.b, changed_only=not args.all)
    counts = diff.get("counts", {})
    print(
        f"regressed {counts.get('regressed', 0)} · fixed {counts.get('fixed', 0)} · "
        f"unchanged {counts.get('unchanged', 0)}"
    )
    # Dataset rows are editable, so a case can differ between runs because the
    # QUESTION changed. Those verdicts aren't comparable, and a gate that stayed
    # silent about them would be reporting on a diff it can't actually read.
    if counts.get("input_changed"):
        print(
            f"  note: {counts['input_changed']} case(s) had their dataset row edited between "
            "these runs — neither a fix nor a regression can be read from them."
        )
    for item in diff.get("items", []):
        if item["status"] in ("regressed", "fixed", "input_changed"):
            print(f"  {item['status']:<13} case {item['ordinal']}: {item['variables']}")
    # A regression between two runs is a failure even though each run "finished".
    # An edited input is not: it is a warning about the comparison, not a defect.
    return EXIT_REGRESSED if counts.get("regressed") else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cubic", description="Cubic command line.")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("CUBIC_API_KEY"),
        help="Defaults to $CUBIC_API_KEY.",
    )
    sub = parser.add_subparsers(dest="group", required=True)
    evals = sub.add_parser("evals", help="Run and inspect evals.").add_subparsers(
        dest="command", required=True
    )

    run = evals.add_parser("run", help="Run an eval and exit non-zero if it regressed.")
    run.add_argument("eval_id", help="An eval… id (or its internal UUID).")
    run.add_argument(
        "--wait",
        action="store_true",
        help="Block until a dataset run finishes. Required for the exit code to mean anything.",
    )
    run.add_argument("--timeout", type=float, default=1800.0, help="Seconds to wait (default 1800).")
    run.add_argument(
        "--fail-on",
        default=DEFAULT_FAIL_ON,
        help=f"Verdicts that exit non-zero (default '{DEFAULT_FAIL_ON}').",
    )
    run.add_argument("--show", type=int, default=10, help="Failing cases to print (default 10).")
    run.add_argument("--quote", action="store_true", help="Print the cost and exit without running.")
    run.set_defaults(func=_run)

    compare = evals.add_parser("compare", help="Diff two runs; non-zero on any regression.")
    compare.add_argument("eval_id")
    compare.add_argument("--a", required=True, help="Baseline run id.")
    compare.add_argument("--b", required=True, help="Compared run id.")
    compare.add_argument("--all", action="store_true", help="Include unchanged cases.")
    compare.set_defaults(func=_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.api_key:
        print("No API key: pass --api-key or set CUBIC_API_KEY.", file=sys.stderr)
        return EXIT_ERROR
    try:
        return args.func(args)
    except TimeoutError as e:
        # Distinct from a regression: the run may still be fine. Exiting 2 lets
        # a pipeline tell "it broke" apart from "we gave up waiting".
        print(f"::error::{e}", file=sys.stderr)
        return EXIT_ERROR
    except CubicError as e:
        print(f"::error::{e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
