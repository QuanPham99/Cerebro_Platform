"""Consumer-owned protocol; independent of semantic enrichment."""
from dataclasses import dataclass
from typing import Protocol, Generic, TypeVar
from pydantic import TypeAdapter
from .query_models import GroundingSnapshot
from .complexity import AcceptedComplexRoute

OutputT=TypeVar('OutputT')

@dataclass(frozen=True,slots=True)
class GuardedGenerationRequest:
    canonical_question: str
    snapshot: GroundingSnapshot
    mode: str='default_ir'
    accepted_complex_route: AcceptedComplexRoute | None=None

@dataclass(frozen=True,slots=True)
class ProviderGeneration(Generic[OutputT]):
    output: OutputT
    transport_attempts: int=1
    input_tokens: int=0
    output_tokens: int=0
    cost_usd: str='0'
    recovered_codes: tuple[str,...]=()

class Text2SQLGenerationProvider(Protocol):
    name: str
    model: str
    model_revision: str
    schema_mechanism: str
    def generate(self,request: GuardedGenerationRequest,adapter: TypeAdapter) -> ProviderGeneration: ...
