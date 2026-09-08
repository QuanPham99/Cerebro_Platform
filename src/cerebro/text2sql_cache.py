"""Scoped in-memory IR cache; never stores SQL, parameters, question or results."""
from .query_models import CachedGeneration
from .provenance import QueryFault,digest
from .complexity import verify_route

class IRCache:
    def __init__(self): self.entries={};self.routes={}
    def get(self,key,snapshot):
        value=self.entries.get(key)
        if value is None: return None
        if digest(value,exclude={'payload_sha256'})!=value.payload_sha256: raise QueryFault('cache_integrity_error')
        route=None
        if value.generation_route=='planned_ir':
            route=self.routes.get(value.accepted_complex_plan_hash)
            verify_route(route,snapshot)
        elif value.accepted_complex_plan_hash is not None: raise QueryFault('cache_integrity_error')
        return value,route
    def put(self,key,ir,route_name,route=None):
        payload=dict(ir=ir,generation_route=route_name,accepted_complex_plan_hash=route.plan_hash if route else None)
        self.entries[key]=CachedGeneration(**payload,payload_sha256=digest(payload))
        if route: self.routes[route.plan_hash]=route


def cache_key(snapshot,question_hash,provider,route,max_rows):
    return digest({'scope':snapshot.authorization_scope_hash,'snapshot':snapshot.snapshot_hash,
                   'policy':snapshot.policy_version,'question':question_hash,'canonicalization':snapshot.canonicalization_version,
                   'literal_registry':snapshot.literal_registry_version,'type_registry':snapshot.type_registry_version,
                   'dialect':snapshot.dialect,'route':route,'provider':provider.name,'model':provider.model,
                   'model_revision':provider.model_revision,'schema':provider.schema_mechanism,
                   'prompt':'008.prompt.v1','ir':'008.ir.v1','router':'008.router.v1','compiler':'008.compiler.v1',
                   'checker':'008.checker.v1','max_rows':max_rows})
