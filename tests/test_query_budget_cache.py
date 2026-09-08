import pytest
from cerebro import query_models as m
from cerebro.query_budget import RequestBudget
from cerebro.complexity import ComplexityRouter,AcceptedComplexRoute
from cerebro.provenance import QueryFault,digest
from cerebro.text2sql_cache import IRCache
from cerebro.evaluation import build_agent
from cerebro.reference import GoldenProvider
from text2sql_factories import context,agent_for,seeded_db


def test_budget_defaults_and_transition_authority():
    _,_,s=context();budget=RequestBudget()
    assert budget.capacity==1 and budget.limits.max_transport_attempts_per_semantic_call==2
    assert budget.limits.end_to_end_deadline_ms==120000
    with pytest.raises(AttributeError): budget.capacity=2
    with pytest.raises(QueryFault): budget.before_call('planned_ir')
    with pytest.raises(QueryFault): budget.authorize_planned_ir({},s)


def test_deadline_blocks_before_provider():
    clock=[0.0];b=RequestBudget(clock=lambda:clock[0]);clock[0]=121.0
    with pytest.raises(QueryFault,match='budget_exceeded'): b.before_call('default_ir')
    assert b.calls==0


def test_budget_third_call_always_rejected():
    _,_,s=context();b=RequestBudget();b.before_call('default_ir')
    plan=m.ComplexQueryPlan(plan_version='008.complex-plan.v1',operator_ids=('window.period_over_period.v1',),steps=(m.ComplexPlanStep(step_id='w',operator_id='window.period_over_period.v1',input_object_ids=('table.customers',),output_names=('rank',)),),expected_outputs=('rank',))
    decision=ComplexityRouter().accept(plan,s);b.authorize_planned_ir(decision,s);b.before_call('planned_ir')
    with pytest.raises(QueryFault): b.authorize_planned_ir(decision,s)
    with pytest.raises(QueryFault): b.before_call('planned_ir')


def test_corrupt_cache_integrity_never_contacts_engine():
    cache=IRCache();a,p,e,r=agent_for(cache=cache);assert a.run(r).status=='ok'
    key=next(iter(cache.entries));cache.entries[key]=cache.entries[key].model_copy(update={'payload_sha256':'0'*64})
    e.calls.clear();out=a.run(r)
    assert out.status=='check_failed' and e.calls==[] and len(p.calls)==1


def test_planned_cache_preserves_route_and_rechecks_engine(tmp_path):
    db=seeded_db(tmp_path/'test.duckdb');resolver,scope,_=context();p=GoldenProvider();a,e=build_agent(db,p,resolver.bundle,IRCache())
    try:
        req=m.SQLGenerationRequest(question='What is monthly transaction growth for the last 24 months?',authorization_scope=scope)
        first=a.run(req);second=a.run(req)
        assert first.status==second.status=='ok'
        assert second.generation_route=='planned_ir' and second.cache_status=='hit'
        assert len(p.calls)==2 and second.budget_usage.semantic_calls==0
        assert first.result.rows==second.result.rows
    finally: e.close()


@pytest.mark.parametrize("mode", ["unknown", "cache", "", None])
def test_budget_rejects_unknown_mode_without_consuming_call(mode):
    budget = RequestBudget()
    with pytest.raises(QueryFault, match="budget_exceeded"):
        budget.before_call(mode)
    assert budget.calls == 0
    budget.before_call("default_ir")
    assert budget.calls == 1


def test_budget_second_call_requires_planned_mode():
    _, _, snapshot = context()
    budget = RequestBudget()
    budget.before_call("default_ir")
    plan = m.ComplexQueryPlan(
        plan_version="008.complex-plan.v1",
        operator_ids=("window.period_over_period.v1",),
        steps=(m.ComplexPlanStep(
            step_id="w", operator_id="window.period_over_period.v1",
            input_object_ids=("table.customers",), output_names=("rank",),
        ),),
        expected_outputs=("rank",),
    )
    decision = ComplexityRouter().accept(plan, snapshot)
    budget.authorize_planned_ir(decision, snapshot)
    for mode in ("default_ir", "unknown", "cache"):
        with pytest.raises(QueryFault, match="budget_exceeded"):
            budget.before_call(mode)
        assert budget.calls == 1
    budget.before_call("planned_ir")
    assert budget.calls == 2
