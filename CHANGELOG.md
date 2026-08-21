# Changelog

## Unreleased

## 0.13.0 (2026-08-21)

- **Group runs the way your job is actually shaped, and tag them.** `run_id` is
  now any string you already hold — a job name, a date, an order number —
  instead of a UUID you had to invent:

  ```python
  client.completions.create(cube_id, {...}, run_id="nightly-2026-08-21")
  ```

  When a run has structure, `run_path` names it root-first, and Logs becomes a
  drill-down: open the run, see its records, open a record to see its
  completions.

  ```python
  for record in records:
      client.completions.create(
          extract_id, {...}, run_path=["nightly-2026-08-21", record.id]
      )
  ```

  Cubic creates whatever levels are missing, so a parent never has to be
  declared first, and re-sending the same path is idempotent. `run_id` is sugar
  for a one-level path; sending both raises, since they are two spellings of one
  field.

  `tags` are the orthogonal dimension — flat labels, any number per call,
  created on first use. Filtering by several narrows, so `urgent` + `eu-region`
  is the urgent EU work, and Usage → Tags costs each slice:

  ```python
  client.completions.create(cube_id, {...}, tags=["urgent", "eu-region"])
  ```

  Both are available on `completions.create` and `completions.render`, sync and
  async, and both are inherited by the nested cubes and polycube nodes a call
  sets off — so a cost-by-run figure covers the whole tree.

- **`CompletionRecord` reads the grouping back.** `record.run` carries your own
  keys and the full path (`["nightly-2026-08-21", "rec-4471"]`), `record.run_id`
  is the node's `run_…` id for the analytics filters, and `record.tags` lists the
  labels. New `RunRef` / `TagRef` types are exported.

- **Breaking:** `run_id` no longer accepts a `uuid.UUID` as a distinct meaning —
  it is stringified like any other key, and the value you get back from
  `CompletionRecord.run_id` is now a `run_…` public id rather than the UUID you
  sent. Ids from 0.12.0 were backfilled as run keys, so existing history is
  still readable in Logs under its old UUID string.

## 0.12.0 (2026-08-20)

- **Say which app is calling, and which workflow a call belongs to.** Two
  dimensions Logs and Usage can now be sliced by. Identify the application once,
  on the client:

  ```python
  client = Cubic(app_url="https://app.example.com", app_title="Example Checkout")
  ```

  That sends `HTTP-Referer` / `X-Title` — the same pair OpenRouter uses, so
  code already setting them needs no change. Cubic groups by the URL's
  registrable domain, so every page of your site is one application. Traffic
  without it is filed as "Unknown", which is now distinguishable from runs you
  made in the Cubic dashboard.

  For a multi-cube workflow, pass one `run_id` to every call:

  ```python
  run = uuid.uuid4()
  client.completions.create(extract_id, {"doc": path}, run_id=run)
  client.completions.create(summarise_id, {"text": ...}, run_id=run)
  ```

  Logs then groups them, nested cubes and polycube nodes included — those
  inherit the run id from the call that caused them. Unlike `client_request_id`,
  which identifies one request and is refused for polycubes, `run_id` identifies
  the workflow and works for both kinds. Any UUID you choose; Cubic never
  interprets it.

- **`default_headers` on both clients**, for anything else you need on every
  request. A per-call header of the same name still wins.

- **`evals compare` reports edited dataset rows.** Rows are editable and carry
  no version, so the same row can hold a different question in each run. The
  server now classifies those cases as `input_changed` instead of counting them
  as fixed or regressed, and the CLI prints how many there were. It does not
  fail the build: an edited input is a warning about the comparison, not a
  defect in the cube.

## 0.11.0 (2026-08-18)

- **Create a cube of any output type.** `cubes.create` gained `output_type` —
  `"text"` (default), `"structured"`, `"image"` or `"audio"` — plus
  `response_format`. Structured cubes could previously only be made in the
  dashboard, and `create_version(response_format=…)` was unusable on anything
  the SDK could create:

  ```python
  scorer = client.cubes.create(
      "Sentiment scorer",
      user_prompt="Score the sentiment of {{text}}.",
      models=[{"provider": "openai", "model_name": "gpt-4o-mini", "rank": 0}],
      output_type="structured",
      response_format={"type": "object",
                       "properties": {"score": {"type": "number"}},
                       "required": ["score"]},
  )
  ```

  The output type is fixed at creation, so mismatched arguments are refused
  locally rather than spending a round trip on a cube you would have to throw
  away. For `"image"`/`"audio"` the medium comes from the model stack;
  `output_type` asserts the stack agrees. Video is not offered — no catalog
  model generates it yet.

- **`cubes.delete(cube_id)`.** The counterpart to an immutable output type: a
  cube created wrong can't be edited into shape, so the fix is delete and
  re-create. Raises `ConflictError` for a listed, polycube-referenced or
  platform cube.

- **`cubes.test` takes `response_format` and `variable_definitions`.** Both are
  unsaved, like the prompt overrides already were, so a schema or a retyped
  variable can be tried before it is committed instead of being tested under the
  saved definition.

- **`Cube.output_kind` and `Cube.is_structured`** on reads — `retrieve` used to
  leave the medium unreportable and structuredness inferable only from
  `response_format_source`.


## 0.10.0 (2026-08-11)

- **Datasets and evals.** `client.datasets` and `client.evals` cover the whole
  loop from code: build a set of input cases, point an eval at it, run it, and
  read the per-case results.

  ```python
  ds = client.datasets.create("Golden set", cube_id=cube_id)
  client.datasets.add_rows(ds.public_id, [{"variables": {"q": "..."}, "expected_output": "..."}])
  client.evals.set_dataset(eval_id, ds.public_id)

  run = client.evals.run(eval_id, wait=True)
  if not run.passed:
      for case in client.evals.cases(run.id, verdict="fail"):
          print(case.ordinal, case.variables, case.rationale)
  ```

  A dataset run is queued on a worker, so `wait=True` polls it to completion
  and `run.passed` is true only when it finished and every case passed. Check
  `client.evals.quote(eval_id)` first if a scheduled job might spend more than
  you expect.

- **A `cubic` CLI, and a GitHub Action.** Evals nobody enforces get ignored
  within a month, so the exit code is the product:

  ```bash
  cubic evals run evalAbC123XyZ0000 --wait
  ```

  Exits 1 when the eval's verdict is `fail` or `error` (and prints the failing
  cases), 2 when it couldn't run or timed out — so a pipeline can tell "it
  broke" apart from "we gave up waiting". `cubic evals compare` fails on any
  regression between two runs. The `action/` directory wraps the same command
  for GitHub Actions.

- Evals are now reachable with an `mxk_` key and addressable by their public
  `eval…` id, which is what makes all of the above possible.

## 0.9.0 (2026-08-09)

- **Run inference yourself.** `client.completions.external(...)` renders a
  cube's prompt, yields everything needed to reproduce the call, and posts your
  result back so the run still lands in Logs, Usage and per-version outcomes:

  ```python
  with client.completions.external(cube_id, {"inquiry": text}) as run:
      resp = openai.chat.completions.create(model=run.model_name, messages=run.messages)
      run.output = resp.choices[0].message.content
      run.usage = resp.usage
  ```

  A raised exception inside the block attaches the failure and re-raises, so
  external error rates stay visible. Batch renders iterate and attach in one
  call. `async with` works the same way on `AsyncCubic`.

  The halves are separately available as `completions.render()` →
  `RenderedPrompt` and `completions.attach(completion_id, ...)`, so the result
  can be posted from a different process.

  Provider `usage` objects from OpenAI and Anthropic are mapped client-side;
  unrecognised shapes leave usage *unknown* rather than zero. Requires a paid
  plan; refused for marketplace cubes you don't own, and for polycubes.

- Attached output is validated against the cube's `response_format` when it
  declares one. A conforming result is stored parsed; a mismatch is recorded but
  reported as `partial` with an `output_validation` error, so an external model
  that quietly stopped honouring the schema is visible. Nothing is repaired —
  repair would mean Cubic re-prompting a model on a path that exists because you
  own the provider calls.

- `cubes.retrieve()` accepts `channel=` alongside `version=`, so you can read
  what a named pointer serves without first listing channels. Passing both
  raises `TypeError` before the request goes out. `cbe_…@staging` ids work here
  too, exactly as they do when running a completion.

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
