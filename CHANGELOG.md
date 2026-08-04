# Changelog

## 0.8.0 (2026-08-04)

- New `ConflictError` for HTTP 409. Previously these fell through to the base
  `CubicError`, so a conflict could only be told apart by reading
  `.status_code`. Nothing breaks — it subclasses `CubicError`, so existing
  handlers still catch it — but conflicts can now be handled by type:

  ```python
  from cubic import ConflictError

  try:
      client.cubes.create(...)
  except ConflictError as e:
      print(e.error_code)   # already_listed | version_unchanged | alias_in_use …
  ```

  A 409 means the request was well-formed but conflicts with current state, so
  retrying it verbatim will keep failing until something else changes — which
  is why it is deliberately not an `InvalidRequestError`.

- Documented **model aliases** in the model catalog and cube-authoring docs. A
  stack entry may be `{"provider": "alias", "model_name": "fast-default"}`,
  naming an alias managed in the dashboard (Setup → Models) that points at a
  real model; Cubic substitutes the target when the run starts, so re-pointing
  it switches every cube using it without republishing.

  No code change was needed — `provider` was already an open string and the SDK
  has never validated model names client-side, so aliases have always passed
  through. What was missing was any hint that they exist. `models.list()` /
  `retrieve()` still cover the catalog only, which never contains aliases.

## 0.7.0 (2026-07-28)

**Breaking:** `completions.create(attachments=[...])` is removed. Files are now
ordinary inputs — declare a variable of type `file` on the cube and pass the
file as that variable's value:

```python
client.completions.create(cube_id, {"contract": Path("lease.pdf")})
```

A value may be a `Path`, `(filename, bytes)`, an `Attachment`, or an `att_…` id;
paths and byte tuples are uploaded automatically, and an `Attachment`/id is
reused as-is (no second upload). Plain strings are never treated as files.
Batch items each bind their own. Passing `attachments=` raises a `CubicError`
explaining the migration rather than failing at the API.

New `cubic.variable()` helper for declaring a cube's inputs when authoring:

```python
client.cubes.create(
    "Contract reviewer",
    user_prompt="Review {{contract}} for {{focus}}",
    variables={
        "contract": variable("file", description="The signed agreement"),
        "focus": variable(required=False),
    },
)
```

`variables=` was already accepted by `cubes.create` / `create_version` / `test`
but undocumented and untyped; it now has a builder, a `VariableType` alias, and
docs in both the docstrings and the README. Undeclared `{{placeholders}}` still
default to required strings, so declare only what differs.

Why the file change: the prompt now decides what happens to a file, which the old
attachments list couldn't express — bare `{{contract}}` places it,
`<<READ::{{contract}}>>` reads it as text, `<<TRANSCRIBE::{{recording}}>>`
transcribes audio or video (a newly supported type).

## 0.6.0 (2026-07-24)

- Binary outputs (Binary Cubes): `result.file` / `result.files` return
  `GeneratedFile` metadata for image/audio-generating cubes (per-completion
  `SingleCompletion.files` too), and the new `client.files` resource downloads
  the bytes — `download(file_or_id)` returns a `DownloadedFile` (bytes +
  media type), `save(file_or_id, path)` writes it to disk (a directory path
  derives the filename). Downloads authenticate through `GET /v1/files/{id}`
  and work for the file's whole 30-day retention, unlike the ~1h presigned
  `url` on the metadata; after retention they raise `NotFoundError` with code
  `file_expired`. Test-mode stub files (data-URI urls) decode locally with no
  HTTP call.
- New types: `GeneratedFile`, `DownloadedFile`.

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
