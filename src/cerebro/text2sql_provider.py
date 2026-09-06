"""Consumer-owned Text-to-SQL provider protocol and sanitized transport records.

This module deliberately knows nothing about the enrichment flow, about any
vendor SDK, or about HTTP. It defines the boundary that orchestration depends
on so transport can be swapped for a scripted or cassette adapter without a
fallback path between them.
"""

from __future__ import annotations

from decimal import Decimal
from typing import (
    Annotated,
    Generic,
    Literal,
    Protocol,
    TypeAlias,
    TypeVar,
    runtime_checkable,
)

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from .models import GuardedGenerationRequest, StrictFrozenModel

OutputT = TypeVar("OutputT")

SanitizedCode: TypeAlias = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")
]


class ProviderConfigurationError(Exception):
    """Raised when runtime provider configuration is absent or unusable."""


class ProviderUnavailable(Exception):
    """Raised when every retryable transport attempt for one call is exhausted."""


class ProviderRejected(Exception):
    """Raised for a sanitized, non-retryable provider rejection."""


class EgressBlocked(Exception):
    """Raised when a payload is not an authorized, locally built request."""


class TransportAttempt(StrictFrozenModel):
    """One sanitized HTTP attempt inside a single semantic call."""

    ordinal: int = Field(ge=1)
    outcome: Literal["accepted", "rejected"]
    status_code: int | None = None
    latency_ms: int = Field(ge=0)
    error_code: SanitizedCode | None = None


class ProviderUsage(StrictFrozenModel):
    """Cumulative usage for one semantic call, from gateway figures when given."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal(0), ge=0, allow_inf_nan=False)


class ProviderGeneration(BaseModel, Generic[OutputT]):
    """A validated provider output plus its sanitized transport evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    output: OutputT
    transport_attempts: tuple[TransportAttempt, ...] = Field(min_length=1)
    usage: ProviderUsage


@runtime_checkable
class Text2SQLGenerationProvider(Protocol):
    """The only provider shape orchestration may consume."""

    provider: str
    model: str
    model_revision: str
    schema_mechanism: str

    def generate(
        self,
        request: GuardedGenerationRequest,
        output_adapter: TypeAdapter[OutputT],
    ) -> ProviderGeneration[OutputT]: ...
