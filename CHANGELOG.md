# Changelog

## 0.3.0 (2026-07-11)

- `client.models` resource: `list()` (public catalog, cached in-process for
  1h, `provider=` filter, `force_refresh=`) and `retrieve(model_name)`
  (client-side lookup with `provider=` disambiguation and did-you-mean
  suggestions via `ModelNotFoundError`).
- PyPI packaging prep: `py.typed` marker (PEP 561), trove classifiers,
  project URLs, changelog.

## 0.2.0 (2026-07-10)

- `AsyncCubic`: the full client surface as coroutines, sharing retry and
  parsing logic with the sync client.
- `completions.wait(request_id)` and the `result.wait()` shortcut: poll for a
  queued run's persisted record with backoff; raises `CompletionError` for
  error records and `WaitTimeoutError` on deadline.
- `cubic.webhooks`: `verify()` / `verify_signature()` / `parse()` for signed
  callback deliveries (`X-Maxwell-Signature`, HMAC-SHA256 over the raw body),
  plus `derive_project_secret()` for self-hosted deployments.

## 0.1.0 (2026-07-09)

- Initial release: `Cubic` client with `completions.create` /
  `completions.retrieve` and `cubes.retrieve`.
- Unified cube/polycube execution: one `create()` call for both kinds, typed
  `CompletionResult` / `PolycubeResult` with a common `result.content`.
- Typed exception hierarchy incl. pipeline errors surfaced from HTTP 200
  bodies (`MissingVariableError`, `ProviderError`, `CubeNotFoundError`…).
- Automatic idempotent retries via auto-generated `client_request_id`.
