import pytest
from pydantic import ValidationError
from cerebro import query_models as m
from cerebro.provenance import digest
from text2sql_factories import agent_for,context,ir_for

def test_strict_ok_roundtrip_and_provenance():
    agent,_,_,request=agent_for();response=agent.run(request)
    assert response.status=='ok'
    assert m.RESPONSE_ADAPTER.validate_json(response.model_dump_json())==response
    assert response.sql_artifact.ir_hash==digest(response.ir)
    assert response.generation_route=='default_ir'
    assert response.result.row_count==1

@pytest.mark.parametrize('field,value',[('result',None),('snapshot_hash',None),('generation_route','none'),('surprise','SQL')])
def test_ok_rejects_missing_evidence_or_extra_fields(field,value):
    agent,_,_,request=agent_for();data=agent.run(request).model_dump();data[field]=value
    with pytest.raises(ValidationError): m.RESPONSE_ADAPTER.validate_python(data)

def test_caller_grounding_is_not_authority():
    _,scope,_=context()
    with pytest.raises(ValidationError): m.SQLGenerationRequest(question='q',authorization_scope=scope,grounding={})

def test_free_form_sql_or_value_is_not_ir():
    for bad in ({'sql':'SELECT 1'},{'kind':'literal','value':'secret'}):
        with pytest.raises(ValidationError): m.OUTCOME_ADAPTER.validate_python(bad)

@pytest.mark.parametrize('rows',[-1,0,1001])
def test_row_cap_bounds(rows):
    _,scope,_=context()
    with pytest.raises(ValidationError): m.SQLGenerationRequest(question='q',authorization_scope=scope,max_rows=rows)

def test_snapshot_is_deeply_frozen():
    _,_,snapshot=context()
    column=next(c for o in snapshot.objects for c in o.columns)
    with pytest.raises(ValidationError): column.ref.column='secret'
    with pytest.raises(ValidationError): snapshot.ranking_evidence[0].score=10

def test_refused_cannot_carry_rows():
    with pytest.raises(ValidationError): m.RefusedResponse(status='refused',result={'rows':[['secret']]})
