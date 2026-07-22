"""Cubic Python SDK.

Quickstart::

    from cubic import Cubic

    client = Cubic(api_key="mxk_...")
    result = client.completions.create(
        cube_id="cbe_a1B2c3D4e5F6g7",
        variables={"customer_name": "Ada"},
    )
    print(result.content)
"""

from . import webhooks
from ._async_client import AsyncCubic
from ._client import Cubic
from ._exceptions import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    CompletionError,
    CompletionNotFoundError,
    CompletionTimeoutError,
    CubeNotFoundError,
    CubicError,
    InsufficientCreditsError,
    InternalServerError,
    InvalidRequestError,
    MissingVariableError,
    ModelNotFoundError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    RateLimitError,
    VersionNotFoundError,
    WaitTimeoutError,
    WebhookSignatureError,
)
from ._version import __version__
from .types import (
    AttemptError,
    CompletionRecord,
    CompletionResult,
    Cube,
    CubeModel,
    CubeVersion,
    Metrics,
    Model,
    Polycube,
    PolycubeEdge,
    PolycubeInput,
    PolycubeNode,
    PolycubeResult,
    Project,
    Segment,
    SingleCompletion,
)

__all__ = [
    "Cubic",
    "AsyncCubic",
    "webhooks",
    "__version__",
    # exceptions
    "CubicError",
    "APIConnectionError",
    "APITimeoutError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "ModelNotFoundError",
    "CubeNotFoundError",
    "VersionNotFoundError",
    "CompletionNotFoundError",
    "InvalidRequestError",
    "InsufficientCreditsError",
    "RateLimitError",
    "CompletionTimeoutError",
    "InternalServerError",
    "CompletionError",
    "MissingVariableError",
    "ProviderError",
    "WaitTimeoutError",
    "WebhookSignatureError",
    # types
    "AttemptError",
    "Metrics",
    "SingleCompletion",
    "CompletionResult",
    "PolycubeResult",
    "Segment",
    "CompletionRecord",
    "Cube",
    "CubeModel",
    "CubeVersion",
    "Model",
    "Project",
    "Polycube",
    "PolycubeNode",
    "PolycubeEdge",
    "PolycubeInput",
]
