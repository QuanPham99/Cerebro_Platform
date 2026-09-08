import pytest
from cerebro import query_models as m
from cerebro.prompting import GuardedProvider
from cerebro.hosted_provider import ScriptedProvider
from cerebro.text2sql_provider import GuardedGenerationRequest
from cerebro.provenance import QueryFault
from text2sql_factories import context,ir_for,QUESTION,agent_for

@pytest.mark.parametrize('payload',['SELECT 1',{}, {'question':'q','rows':[['SYNTHETIC_PRIVATE']]}])
def test_forged_envelopes_fail_before_inner_contact(payload):
    inner=ScriptedProvider([ir_for()])
    with pytest.raises(QueryFault,match='egress_blocked'): GuardedProvider(inner).generate(payload,m.OUTCOME_ADAPTER)
    assert inner.calls==[]

@pytest.mark.parametrize('question',['Find person@example.invalid','Find phone 84912345678','Find "SYNTHETIC_PRIVATE"'])
def test_ingress_blocks_identifiers_before_provider(question):
    a,p,e,r=agent_for();out=a.run(r.model_copy(update={'question':question}))
    assert out.status=='check_failed' and out.violations[-1].code=='egress_blocked'
    assert p.calls==e.calls==[]

def test_valid_envelope_contains_only_canonical_question_and_snapshot():
    import json
    a,p,e,r=agent_for();assert a.run(r).status=='ok'
    envelope=json.loads(p.calls[0][1])
    assert set(envelope)=={'question','snapshot','mode'}
    assert 'result' not in envelope and 'sql' not in envelope and 'parameters' not in envelope
