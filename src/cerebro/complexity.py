"""Locally issued, snapshot-bound authority for the only second model call."""
from dataclasses import dataclass
from .provenance import QueryFault,digest
from .query_models import ComplexQueryPlan

_TOKEN=object()
OPERATORS={'window':'window.period_over_period.v1','set_operation':'set_operation.safe_binary.v1'}

@dataclass(frozen=True,slots=True,init=False)
class AcceptedComplexRoute:
    snapshot_hash: str
    plan_hash: str
    plan: ComplexQueryPlan
    _token: object


def verify_route(route,snapshot):
    if type(route) is not AcceptedComplexRoute or getattr(route,'_token',None) is not _TOKEN or route.snapshot_hash!=snapshot.snapshot_hash or digest(route.plan)!=route.plan_hash:
        raise QueryFault('invalid_complex_route')


class ComplexityRouter:
    version='008.router.v1'

    def accept(self,plan,snapshot):
        objects={o.object_id for o in snapshot.objects}
        if not plan.steps or not plan.expected_outputs or len(set(plan.expected_outputs))!=len(plan.expected_outputs):
            raise QueryFault('unsupported_complexity')
        seen=set()
        for step in plan.steps:
            if step.step_id in seen or not set(step.depends_on)<=seen or not step.input_object_ids or not set(step.input_object_ids)<=objects or not step.output_names:
                raise QueryFault('unsupported_complexity')
            if seen and not step.depends_on:
                raise QueryFault('unsupported_complexity')
            seen.add(step.step_id)
        if set(plan.operator_ids)!={s.operator_id for s in plan.steps}:
            raise QueryFault('unsupported_complexity')
        route=object.__new__(AcceptedComplexRoute)
        for name,value in [('snapshot_hash',snapshot.snapshot_hash),('plan_hash',digest(plan)),('plan',plan),('_token',_TOKEN)]:
            object.__setattr__(route,name,value)
        return route

    def check_fallback(self,ir,route,snapshot):
        verify_route(route,snapshot)
        nodes={n.node_id:n for n in ir.nodes}
        complex_nodes={n.node_id:n for n in ir.nodes if n.kind in OPERATORS}
        if set(complex_nodes)!={s.step_id for s in route.plan.steps}:
            raise QueryFault('fallback_plan_mismatch')
        def ancestors(nid,seen=None):
            seen=set() if seen is None else seen
            if nid in seen or nid not in nodes: return seen
            seen.add(nid); n=nodes[nid]
            for key in ('input_id','left_id','right_id'):
                child=getattr(n,key,None)
                if child: ancestors(child,seen)
            return seen
        for step in route.plan.steps:
            node=complex_nodes[step.step_id]
            reachable=ancestors(node.node_id)
            tables={nodes[x].table_id for x in reachable if nodes[x].kind=='scan'}
            if OPERATORS[node.kind]!=step.operator_id or not set(step.depends_on)<=reachable or not set(step.input_object_ids)<=tables:
                raise QueryFault('fallback_plan_mismatch')
            if node.kind=='window' and tuple(x.alias for x in node.outputs)!=step.output_names:
                raise QueryFault('fallback_plan_mismatch')
        def output_names(node_id,visited=None):
            visited=set() if visited is None else visited
            if node_id not in nodes or node_id in visited: raise QueryFault('fallback_plan_mismatch')
            visited.add(node_id); node=nodes[node_id]
            if node.kind in ('sort','limit','filter'): return output_names(node.input_id,visited)
            if node.kind=='set_operation': return output_names(node.left_id,visited)
            if node.kind=='aggregate': return tuple(x.alias for x in (*node.group_by,*node.measures))
            if node.kind=='project': return tuple(x.alias for x in node.outputs)
            if node.kind=='window': return output_names(node.input_id,visited)+tuple(x.alias for x in node.outputs)
            raise QueryFault('fallback_plan_mismatch')
        if output_names(ir.root_node_id)!=route.plan.expected_outputs:
            raise QueryFault('fallback_plan_mismatch')
        for step in route.plan.steps:
            if nodes[step.step_id].kind=='set_operation' and output_names(step.step_id)!=step.output_names:
                raise QueryFault('fallback_plan_mismatch')
