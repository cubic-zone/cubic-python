# Cubic Python SDK

Run your Cubes and Polycubes from any Python application with a single API key.

```bash
pip install cubic-sdk    # installs the `cubic` import package
```

## Quickstart

```python
from cubic import Cubic

client = Cubic(api_key="mxk_...")  # or set CUBIC_API_KEY

result = client.completions.create(
    cube_id="cbe_a1B2c3D4e5F6g7",
    variables={"customer_name": "Ada", "issue": "billing"},
)
print(result.content)
```

`cube_id` accepts any public Cube ID — plain cubes and Polycubes share one ID
space (`cbe_…`, with legacy `prmt…`/`poly…` IDs still valid). You never need to
know which kind an ID is: the same call runs either, and `result.content` is
the delivered output for both.

## Running a completion

```python
result = client.completions.create(
    cube_id="cbe_a1B2c3D4e5F6g7",
    variables={"customer_name": "Ada"},

    # plain cubes only:
    version=5,                                # pin a version (default: latest)
    parameters={"temperature": 0.7},          # merged over the cube's parameters
    models=[{"provider": "anthropic", "model_name": "claude-sonnet-4-5"}],
    history=[{"role": "user", "content": "hi"}],

    # both kinds:
    test_mode=True,                           # no provider spend, no credit debit
    metadata={"trace": "abc"},
)

result.content            # str | dict — the winning completion (or final node output)
result.kind               # "cube" | "polycube"
result.metrics            # tokens, cost, credits_charged, latency, cache hits
result.request_id         # keep this for retrieval / support
result.is_partial         # cube delivered content but some fallbacks failed
result.segments           # polycube only: per-node outputs, metrics, errors
```

## Files

A file reaches a cube as the value of a **file-typed variable** — the cube
declares `contract` as a file, and you pass one. What happens to it is the
cube's prompt to decide: placed directly in the prompt (PDFs and images go to
the model natively, MD/TXT/RTF/SVG as text), read with `<<READ::{{contract}}>>`
for Office documents, or transcribed with `<<TRANSCRIBE::{{recording}}>>` for
audio and video. The real type is sniffed from the bytes; a run is capped at
50MB / 20 files.

```python
from pathlib import Path

# Pass the file as the variable's value — the SDK uploads it for you.
result = client.completions.create(
    cube_id="cbe_...",
    variables={"contract": Path("q4-report.pdf"), "question": "What were Q4 margins?"},
)

# Or upload once and reuse the att_… id across runs for 7 days. A document a
# cube READs is converted — and charged — once, so re-runs are cheaper.
att = client.attachments.upload("q4-report.pdf")
att.tier                  # "native" | "text" | "extraction" | "audio"
result = client.completions.create(cube_id="cbe_...", variables={"contract": att})
```

A variable's value may be a `Path`, `(filename, bytes)`, an `Attachment`, or an
`att_…` id. A plain string is never treated as a file — it is the variable's
text — so nothing is uploaded by accident.

A file placed *directly* in the prompt requires every model in the cube's stack
to accept that input type; incompatible stacks are rejected with a 422
(`attachment_not_supported`) before any spend. A file handed to a marker needs
no such support: it reaches the model as text.

> **Migrating from 0.6.x:** the `attachments=[…]` argument is gone. Declare a
> file variable on the cube and pass the file as that variable's value; the SDK
> raises a `CubicError` with this instruction if the old argument is used.

## Binary outputs (Binary Cubes)

A cube built on an image- or audio-generating model returns files instead of
text. Each generated file's metadata (id, MIME type, size, short-lived download
URL) rides the normal result; the bytes live for 30 days.

```python
result = client.completions.create(
    cube_id="cbe_...",                     # e.g. a Gemini image-model cube
    variables={"subject": "a red lighthouse"},
)

f = result.file                            # GeneratedFile (single-output runs)
f.media_type                               # "image/png"
f.url                                      # presigned link (~1 hour)

client.files.save(f, "poster.png")         # durable download via your API key
raw = client.files.download(f)             # DownloadedFile: .data, .media_type

result.files                               # every file (broadcast/batch runs)
```

Downloads via `client.files` work for the whole 30-day retention; afterwards
they raise `NotFoundError` with code `file_expired` (the metadata remains on
the run's record, marked `status="expired"`). Test-mode runs return a built-in
stub file that `download()` decodes locally at zero cost.

Plain-HTTP consumers can also pass `?response=file` on `POST /v1/completions`
(single-file fallback cubes) to get the raw bytes as the response body — the
SDK's envelope + `files.download` covers the same need with metrics intact.

## Batch runs

Pass a list of `{id, variables}` items and read the outputs back by your ids:

```python
result = client.completions.create(
    cube_id="cbe_...",
    variables=[
        {"id": "a", "variables": {"text": "first"}},
        {"id": "b", "variables": {"text": "second"}},
    ],
)
result.contents            # {"a": "...", "b": "..."} — delivered items only
result.is_partial          # True if some items failed
```

On a partial batch, failed items are absent from `contents`; find them (with
their errors) in `result.attempts` via `batch_item_id`. `result.content`
(singular) raises on batch results — there is no single winner to return.

## Async client

`AsyncCubic` has the identical surface with awaitable methods:

```python
from cubic import AsyncCubic

async with AsyncCubic(api_key="mxk_...") as client:
    result = await client.completions.create(cube_id="cbe_...", variables={...})
```

## Async execution (callbacks)

If the cube defines a callback URL — or you pass `callback_url=` — the run is
queued and delivered to your endpoint when done:

```python
job = client.completions.create(cube_id="cbe_...", variables={...},
                                callback_url="https://you.example/hook")
job.is_queued      # True
job.request_id     # correlate with the X-Maxwell-Request-Id callback header

record = job.wait(timeout=120)                       # poll until persisted
record = client.completions.wait(job.request_id)     # same, by id
record = client.completions.retrieve(job.request_id) # single poll
```

`wait()` backs off from 0.5s to 4s between polls (fix it with
`poll_interval=`), raises `CompletionError` if the run finished with status
`error`, and `WaitTimeoutError` if nothing was persisted in time (the run
itself is unaffected). With `AsyncCubic`, `await job.wait()`.

## Verifying callback deliveries

Every delivery is signed. Verify and parse it in one step — always against the
**raw request body bytes**:

```python
from cubic import webhooks

@app.post("/hook")                        # any framework
async def hook(request):
    result = webhooks.verify(await request.body(), request.headers,
                             secret=CUBIC_SIGNING_SECRET)
    if result.status == "success":
        handle(result.content)            # CompletionResult | PolycubeResult
```

A bad or missing `X-Maxwell-Signature` raises `WebhookSignatureError`. Unlike
`create()`, an error-status delivery is returned (not raised) — it's an event
you inspect. Retried deliveries reuse the same body and signature and carry an
incrementing `X-Maxwell-Delivery-Attempt` header; deduplicate on
`result.request_id` if your handler isn't idempotent.

## Reading a cube's definition

```python
cube = client.cubes.retrieve("cbe_a1B2c3D4e5F6g7")                      # latest
cube = client.cubes.retrieve("cbe_a1B2c3D4e5F6g7", version=5)           # pinned
cube = client.cubes.retrieve("cbe_a1B2c3D4e5F6g7", channel="staging")   # a pointer

cube.system_instructions   # the cube's system prompt
cube.user_prompt           # the user prompt template
cube.variables             # input schema — handy for pre-flight checks
cube.models                # the model stack (provider, model, rank, role)
```

Reading is free and records nothing. Prompts come back exactly as written —
`{{variables}}` and function markers unsubstituted. With neither argument you
get what `production` serves.

Definitions are owner-only: marketplace cubes you subscribe to can be *run*
but not read, and polycube definitions are not yet available on this endpoint.

## Running inference yourself

Use Cubic for prompt management and versioning while calling your own provider.
`external()` renders the prompt, hands you everything needed to reproduce the
call, and posts your result back so the run still lands in Logs, Usage and
per-version outcomes:

```python
with client.completions.external(cube_id, {"inquiry": text}) as run:
    resp = openai.chat.completions.create(model=run.model_name, messages=run.messages)
    run.output = resp.choices[0].message.content
    run.usage = resp.usage          # OpenAI and Anthropic shapes both map
```

If the block raises, the failure is attached and re-raised — external error
rates stay visible instead of looking like runs nobody finished.

`run.models` is the full stack in fallback order, `run.parameters` the cube's
settings, and `run.response_format` its compiled JSON Schema, so a
structured-output cube works the same way. Batch renders iterate, and attach
every item in one call:

```python
with client.completions.external(cube_id, [{"id": "a", "variables": {...}}]) as batch:
    for item in batch:
        item.output = run_your_model(item.messages)
```

The two halves are also available separately — `client.completions.render(...)`
returns a `RenderedPrompt` carrying a `completion_id`, and
`client.completions.attach(completion_id, output=...)` posts back to it later,
from a different process if you like.

Supply `provider`/`model_name` and token counts and Cubic prices the call from
its model catalog, so inference you paid for directly still shows true cost.
Omit them and usage stays *unknown* rather than becoming zero.

If the cube declares a `response_format`, attached output is validated against
it. Conforming output is stored parsed; output that misses the schema is still
recorded, but the run comes back `partial` with an `output_validation` error
rather than looking like a clean success. Validation never repairs — on this
path the provider calls are yours.

Rendering bills for what it actually consumed — function markers, resource
lookups and nested cubes all still execute, since they are what produces the
text — and the attach itself is free. Requires a paid plan; refused for
marketplace cubes you don't own, and for polycubes.

To read a prompt *without* rendering it — unsubstituted, free, no record —
use `client.cubes.retrieve()` above.

## Authoring cubes

The full authoring lifecycle works by API key — create a cube, iterate its
wording without saving, commit the winner as a version, and roll back if a
change regresses. This is designed for LLM-driven authoring as much as for
scripts.

```python
# Where will it live? (optional — defaults to the key's created-in project)
projects = client.projects.list()                    # public prj_… ids

cube = client.cubes.create(
    "Support reply drafter",
    system_instructions="You are a courteous support agent for ACME.",
    user_prompt="Draft a reply to {{customer_name}} about {{issue}}.",
    models=[{"provider": "openai", "model_name": "gpt-4o-mini", "rank": 0}],
    project_id=projects[0].project_id,
)
# cube.cube_id → "cbe_…", version 1 / "1.0.0", immediately runnable
```

### Declaring inputs

Every `{{placeholder}}` in the content becomes an input automatically, typed as
a required string. Declare `variables` only where you want something else — a
different type, an optional input, or a description that ships with the cube:

```python
from cubic import variable

cube = client.cubes.create(
    "Contract reviewer",
    user_prompt="Review {{contract}} focusing on {{focus}}. Reply in {{language}}.",
    models=[{"provider": "openai", "model_name": "gpt-4o-mini", "rank": 0}],
    variables={
        "contract": variable("file", description="The signed agreement"),
        "focus": variable(required=False),
        # "language" is left undeclared → required string
    },
)
```

Types are `string` (default), `integer`, `float`, `boolean` and `file`.
`variable()` just builds the dict — `{"type": "file", "required": True}` works
too. Declarations are versioned content, so changing one goes through
`create_version` (not `update`, which covers prompt-level settings), and `test`
accepts them for trying a declaration before committing it.

**`file` is what lets a variable take a file.** Passing an `att_…` id to any
other variable is rejected (`undeclared_file_variable`) rather than
dereferenced — variables routinely carry text from your own end users, so a
value can't promote itself into a file reference.

Iterate wording at zero version cost — `test` runs synchronously, bypasses any
callback URL, and never saves:

```python
candidate = "Draft a warm, concise reply to {{customer_name}} about {{issue}}."
result = client.cubes.test(
    cube.cube_id,
    variables={"customer_name": "Ada", "issue": "billing"},
    user_prompt=candidate,          # UNSAVED override; variables re-extracted
)
# judge result.content … loop with new candidates until satisfied, then:
v = client.cubes.create_version(
    cube.cube_id,
    system_instructions=cube.system_instructions,   # a version is a full snapshot
    user_prompt=candidate,
)
# v.version → e.g. "1.0.1" — the server sizes the semantic bump to the delta
# (patch < 5% changed ≤ minor < 40% ≤ major); v.change_ratio tells you how big
# your edit measured.
```

Config changes (never versioned) and history:

```python
client.cubes.update(cube.cube_id, title="Support drafter v2",
                    models=[{"provider": "anthropic",
                             "model_name": "claude-haiku-4-5", "rank": 0}])
client.cubes.versions(cube.cube_id)                  # newest first, is_current flag
client.cubes.set_current_version(cube.cube_id, 1)    # rollback — pointer only
```

`create` attaches an `Idempotency-Key` automatically, so a retried create
replays the original cube instead of minting a duplicate. Cube writes share a
per-user rate limit (60/min) — plenty for iteration loops, bounded against
runaways. To move a cube to another project:
`client.cubes.update(cube.cube_id, project_id="prj_…")`.

## Authoring polycubes

A polycube chains cubes into a DAG — each edge maps one node's output onto a
downstream node's variable. No versions: the graph is the definition.

```python
poly = client.polycubes.create(
    "Draft and polish",
    nodes=[
        {"node_key": "draft",  "cube_id": drafter.cube_id},
        {"node_key": "polish", "cube_id": polisher.cube_id, "version": 2},  # pinned
    ],
    edges=[
        # draft's whole output feeds polisher's {{draft}} variable; set
        # "source_field" to pick one response-format field instead.
        {"source_node_key": "draft", "target_node_key": "polish",
         "target_variable": "draft"},
    ],
)
poly.inputs                      # derived signature — what a run must supply
client.completions.create(cube_id=poly.polycube_id, variables={"topic": "the sea"})

client.polycubes.retrieve(poly.polycube_id)
client.polycubes.update(poly.polycube_id, nodes=[...], edges=[...])  # wholesale replace
```

Every node's cube must live in the polycube's project and use the fallback
strategy; the graph must be acyclic (`chain_graph_cycle` on a 422 otherwise).

## The model catalog

```python
models = client.models.list()                        # cached in-process for 1h
anthropic = client.models.list(provider="anthropic")

m = client.models.retrieve("claude-3-5-haiku")       # ModelNotFoundError suggests
m.context_window, m.input_per_1k, m.supports_tools   # close matches on typos
```

Useful for validating `models=` overrides before a run, populating model
pickers, and estimating cost. Lookups are explicit — the SDK never
auto-validates overrides against the cache; the server stays authoritative.

### Model aliases

A stack entry can name a **model alias** instead of a model — a name you manage
in the dashboard under Setup → Models that points at a real model:

```python
client.cubes.create(
    "Summariser",
    user_prompt="Summarise {{doc}}",
    models=[{"provider": "alias", "model_name": "fast-default", "rank": 0}],
)
```

Cubic substitutes the target when the run starts, so re-pointing the alias
switches every cube using it at once — no editing, no republishing. Results
always report the model that actually ran, never the alias.

Aliases are yours, not the catalog's: `models.list()` never returns one and
`models.retrieve()` won't resolve one. That only limits lookup — since nothing
is validated client-side, an alias passes through `models=` and cube stacks
untouched. Naming one you don't have raises `InvalidRequestError` with
`error_code="alias_not_found"`. Marketplace listings must be alias-free.

## Error handling

The SDK never returns a silent failure: pipeline errors that the API reports
inside an HTTP 200 are raised as typed exceptions too.

```python
from cubic import (
    AuthenticationError,       # bad/expired API key
    CubeNotFoundError,         # unknown ID, or not yours
    MissingVariableError,      # e.missing variable_name
    InvalidRequestError,       # bad parameters / polycube-inapplicable fields
    ConflictError,             # 409 — valid request, conflicts with current state
    InsufficientCreditsError,  # e.required / e.balance / e.topup_allowed
    RateLimitError,            # capacity (auto-retried first)
    ProviderError,             # all model attempts failed; see e.attempt_errors
    CompletionTimeoutError,    # server-side execution deadline exceeded
)

try:
    result = client.completions.create(cube_id="cbe_...", variables={...})
except MissingVariableError as e:
    print(f"Provide the '{e.variable_name}' variable")
except InsufficientCreditsError as e:
    print(f"Need {e.required} credits, have {e.balance}")
```

Every exception carries `.error_code`, `.status_code`, `.request_id` (quote it
in support requests), and — for pipeline failures — `.result` with the full
parsed response.

## Retries and idempotency

Connection failures and capacity 429s are retried automatically with
exponential backoff (`max_retries=2` by default). For plain cubes the SDK
attaches a `client_request_id` idempotency key to every run, so retries can
never double-charge or double-execute; ambiguous failures (timeouts, 5xx) are
retried only when that key is present. Pass your own `client_request_id` to
make retries idempotent across process restarts too.

## Configuration

```python
client = Cubic(
    api_key="mxk_...",                   # or CUBIC_API_KEY
    base_url="http://localhost:8010",    # or CUBIC_BASE_URL (default: https://api.cubic.zone)
    timeout=120.0,
    max_retries=3,
    max_connections=200,                 # connection-pool sizing for services
    max_keepalive_connections=50,        #   (defaults: httpx's 100/20)
)
```
