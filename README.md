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
    base_url="https://api.your-cubic",   # or CUBIC_BASE_URL (default: http://localhost:8010)
    timeout=120.0,
    max_retries=3,
)
```
