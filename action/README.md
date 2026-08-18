# Cubic eval gate

Fail a build when a Cube regresses.

```yaml
- uses: cubic-zone/cubic-python/action@v1
  with:
    eval-id: evalAbC123XyZ0000
    api-key: ${{ secrets.CUBIC_API_KEY }}
```

The step exits non-zero when the eval's verdict is `fail` or `error`, and prints
the failing cases — inputs and the grader's reason — into the job log, so the
first place you look is the log rather than a browser tab.

`error` is in the default `fail-on` deliberately: a run whose cases could not be
judged has told you nothing, and treating silence as success is how a gate
quietly stops gating.
