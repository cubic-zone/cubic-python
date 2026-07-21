# Changelog

## 0.3.3 (2026-07-12)

- `Cubic` and `AsyncCubic` accept `max_connections` / `max_keepalive_connections`
  to size the SDK-owned connection pool (long-lived service deployments) while
  keeping the SDK's completion-sized timeout. Combining them with a
  bring-your-own `http_client` raises — configure `httpx.Limits` there instead.

## 0.3.2 (2026-07-12)

- First PyPI release: `pip install cubic-sdk`.
- Repository moved to the `cubic-zone` GitHub organization; project URLs
  updated. CI and trusted-publishing workflows added. No code changes.

## 0.3.1 (2026-07-12)

- The default `base_url` is now the hosted API (`https://api.cubic.zone`)
  instead of the local dev server. Local development now requires an explicit
  `base_url="http://localhost:8010"` or `CUBIC_BASE_URL`.

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
