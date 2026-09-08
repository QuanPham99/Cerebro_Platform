"""Single metadata-only egress composition boundary, with no sampled values."""
import json
import re
from .complexity import verify_route
from .grounding import verify_snapshot
from .provenance import QueryFault,canonical_json_bytes
from .query_models import canonicalize_question
from .text2sql_provider import GuardedGenerationRequest


def validate_ingress(question):
    if not question or len(question)>8000:
        raise QueryFault('invalid_question')
    # Conservative default; this is an input policy, not an arbitrary PII detector.
    if re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:\+?\d[\s()-]*){7,}|[\"\']',question):
        raise QueryFault('egress_blocked')


class GuardedProvider:
    def __init__(self,inner):
        self.inner=inner
        self.name=inner.name
        self.model=inner.model
        self.model_revision=inner.model_revision
        self.schema_mechanism=inner.schema_mechanism

    def generate(self,request,adapter,*,budget=None):
        if type(request) is not GuardedGenerationRequest:
            raise QueryFault('egress_blocked')
        verify_snapshot(request.snapshot)
        if request.canonical_question!=canonicalize_question(request.canonical_question):
            raise QueryFault('egress_blocked')
        validate_ingress(request.canonical_question)
        if request.mode=='provider_probe':
            if request.snapshot.objects or request.accepted_complex_route is not None or request.canonical_question!='Identify missing table.atms':
                raise QueryFault('egress_blocked')
        elif request.mode=='planned_ir':
            verify_route(request.accepted_complex_route,request.snapshot)
        elif request.mode!='default_ir' or request.accepted_complex_route is not None:
            raise QueryFault('egress_blocked')
        # Model output must use exact snapshot identifiers; no raw caller metadata,
        # error prose, SQL, params, result, ordinal, or pre-rendered envelope enters.
        envelope={'question':request.canonical_question,'mode':request.mode,
                  'snapshot':request.snapshot.model_dump(mode='json')}
        if request.accepted_complex_route is not None:
            envelope['complex_plan']=request.accepted_complex_route.plan.model_dump(mode='json')
        return self.inner.generate_envelope(canonical_json_bytes(envelope).decode(),adapter,request.mode,budget=budget)
