import pytest
from cerebro import query_models as m
from cerebro.provenance import QueryFault
from cerebro.text2sql import Text2SQLAgent
from cerebro.text2sql_cache import IRCache
from text2sql_factories import agent_for,ir_for

def test_default_is_one_call_with_mandatory_engine_phases():
    agent,provider,engine,request=agent_for();response=agent.run(request)
    assert response.status=='ok'
    assert len(provider.calls)==1 and engine.calls==['explain','execute']
    assert response.budget_usage.semantic_calls==1

def test_dependencies_are_mandatory():
    with pytest.raises((ValueError,TypeError)): Text2SQLAgent(None,None,None)

def test_provider_failure_is_typed_and_not_retried():
    agent,p,e,r=agent_for([QueryFault('provider_unavailable')]);out=agent.run(r)
    assert out.status=='check_failed' and out.violations[-1].code=='provider_unavailable'
    assert len(p.calls)==1 and e.calls==[]

def test_engine_failure_never_recalls_provider_or_leaks_error():
    from text2sql_factories import FakeEngine
    agent,p,e,r=agent_for(engine=FakeEngine(fault=QueryFault('execution_error','SYNTHETIC_PRIVATE')))
    out=agent.run(r)
    assert out.status=='check_failed' and len(p.calls)==1
    assert 'SYNTHETIC_PRIVATE' not in out.model_dump_json()
    assert 'SYNTHETIC_PRIVATE' not in p.calls[0][1]

def test_false_missing_grounding_claim_is_failed():
    proposal=m.GroundingRefusal(outcome='grounding_refusal',unmet_needs=(m.GroundingNeed(object_id='table.customers'),))
    a,p,e,r=agent_for([proposal]);out=a.run(r)
    assert out.status=='check_failed' and e.calls==[]

def test_real_missing_grounding_refuses_without_engine():
    proposal=m.GroundingRefusal(unmet_needs=(m.GroundingNeed(object_id='table.atms'),))
    a,p,e,r=agent_for([proposal]);out=a.run(r)
    assert out.status=='refused' and out.reason=='missing_grounding' and e.calls==[]

def test_cache_hit_makes_zero_additional_calls_and_reruns_engine():
    a,p,e,r=agent_for(cache=IRCache());first=a.run(r);second=a.run(r)
    assert first.status==second.status=='ok'
    assert second.cache_status=='hit' and second.generation_route=='default_ir'
    assert second.budget_usage.semantic_calls==0 and len(p.calls)==1
    assert e.calls==['explain','execute']*2

def test_cache_scope_change_misses():
    from cerebro.grounding import trusted_scope
    a,p,e,r=agent_for(outputs=[ir_for(),ir_for()],cache=IRCache())
    assert a.run(r).status=='ok'
    second=a.run(r.model_copy(update={'authorization_scope':trusted_scope(a.resolver.bundle,tenant='another')}))
    assert second.cache_status=='miss' and len(p.calls)==2

def test_no_partial_result_on_compiler_defect():
    a,p,e,r=agent_for();original=a.compiler.compile
    def corrupt(*args):
        from dataclasses import replace
        result=original(*args)
        return replace(result,query=result.query.model_copy(update={'sql':'SELECT 1'}))
    a.compiler.compile=corrupt
    out=a.run(r)
    assert out.status=='check_failed' and e.calls==[]
    assert out.violations[-1].code=='compiled_sql_containment_error'
