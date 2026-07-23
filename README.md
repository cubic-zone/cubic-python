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

## Attachments

Attach files to a run — PDFs and images go to the model natively, MD/TXT/RTF/SVG
are injected into the prompt as text, and Office files (DOCX/PPTX/XLSX) are
text-extracted server-side. The real type is sniffed from the bytes; the total
per run is capped at 50MB / 20 files.

```python
from pathlib import Path

# One-shot: pass files directly (sent inline with the request)
result = client.completions.create(
    cube_id="cbe_...",
    variables={"question": "What were Q4 margins?"},
    attachments=[Path("q4-report.pdf"), ("notes.md", b"# context")],
)

# Reusable: upload once, reference the att_… id across runs for 7 days.
# Office files are extracted once per attachment, so re-runs are cheaper.
att = client.attachments.upload("q4-report.pdf")
att.tier                  # "native" | "text" | "extraction" — how it will be handled
result = client.completions.create(cube_id="cbe_...", attachments=[att])
```

Native-tier files (PDF/images) require every model in the cube's stack to
support that input type — incompatible stacks are rejected with a 422
(`attachment_not_supported`) before any spend. Polycubes accept attachments
too: they're delivered to the chain's first cube.

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
cube = client.cubes.retrieve("cbe_a1B2c3D4e5F6g7")          # latest
cube = client.cubes.retrieve("cbe_a1B2c3D4e5F6g7", version=5)

cube.system_instructions   # the cube's system prompt
cube.user_prompt           # the user prompt template
cube.variables             # input schema — handy for pre-flight checks
cube.models                # the model stack (provider, model, rank, role)
```

Definitions are owner-only: marketplace cubes you subscribe to can be *run*
but not read, and polycube definitions are not yet available on this endpoint.

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

## Error handling

The SDK never returns a silent failure: pipeline errors that the API reports
inside an HTTP 200 are raised as typed exceptions too.

```python
from cubic import (
    AuthenticationError,       # bad/expired API key
    CubeNotFoundError,         # unknown ID, or not yours
    MissingVariableError,      # e.missing variable_name
    InvalidRequestError,       # bad parameters / polycube-inapplicable fields
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
