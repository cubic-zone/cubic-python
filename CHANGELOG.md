# Changelog

## 0.5.0 (2026-07-23)

- Attachments: new `client.attachments` resource (`upload`, `retrieve`,
  `delete`) and an `attachments=` parameter on `completions.create` for both
  clients. Entries may be `att_…` ids, `Attachment` objects, `pathlib.Path`s,
  or `(filename, bytes)` tuples — the latter two are sent inline (base64).
  Works for cubes and polycubes (a polycube delivers attachments to its first
  cube). PDFs and images go to the model natively (every model in the stack
  must support them); MD/TXT/RTF/SVG are injected as text; DOCX/PPTX/XLSX are
  text-extracted server-side, cached per attachment. Bytes are retained for
  7 days; ids stay reusable across runs in that window.
- New type: `Attachment`.
- `request()` on both clients accepts `files=` (multipart).

## 0.4.0 (2026-07-22)

- Cube authoring by API key: `cubes.create`, `cubes.update`, `cubes.test`
  (unsaved content overrides — the prompt-iteration primitive),
  `cubes.create_version` (server-sized semantic version bumps),
  `cubes.versions`, and `cubes.set_current_version` (rollback), on both
  clients. `cubes.create` auto-attaches an `Idempotency-Key` so retries
  replay instead of duplicating.
- New `client.projects.list()` — public `prj_…` project ids, the placement
  targets for `cubes.create`.
- Polycube authoring: `polycubes.create/retrieve/update` — build and edit
  DAGs of cubes by API key (nodes accept `cube_id`/`version`, translated to
  the wire contract). `cubes.update(project_id=…)` moves a cube between
  projects.
- New types: `CubeVersion`, `Project`, `Polycube` (+ node/edge/input shapes).

## 0.3.4 (2026-07-13)

- Batch results: new `result.contents` — outputs as a dict keyed by your
  batch item ids — plus `result.is_batch`.
- Breaking (batch only): `result.content` now raises on a batch result
  instead of silently returning the first item's content. Single-run
  behavior is unchanged.

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
