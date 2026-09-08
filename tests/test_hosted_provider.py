import httpx
import pytest
from cerebro.hosted_provider import HostedProvider,ScriptedProvider,CassetteProvider,provider_from_environment
from cerebro import query_models as m
from cerebro.prompting import GuardedProvider
from cerebro.text2sql_provider import GuardedGenerationRequest
from cerebro.provenance import QueryFault
from text2sql_factories import context,ir_for,QUESTION

def test_organizer_schema_bound_transport_and_retry():
    calls=[]
    def handle(request):
        calls.append(request)
        if len(calls)==1: return httpx.Response(503)
        return httpx.Response(200,json={'choices':[{'message':{'content':ir_for().model_dump_json()}}],'usage':{'prompt_tokens':100,'completion_tokens':50}})
    client=httpx.Client(base_url='https://organizer.invalid/v1',transport=httpx.MockTransport(handle))
    p=HostedProvider('test','SECRET_KEY','https://organizer.invalid/v1','r1',client=client,sleep=lambda _:None)
    _,_,s=context();result=GuardedProvider(p).generate(GuardedGenerationRequest(QUESTION,s),m.OUTCOME_ADAPTER)
    assert result.output==ir_for() and result.transport_attempts==2
    assert result.recovered_codes==('provider_retryable_status',)
    assert 'SECRET_KEY' not in repr(p)

@pytest.mark.parametrize('status,code',[(401,'provider_rejected'),(503,'provider_unavailable')])
def test_transport_failure_sanitized(status,code):
    client=httpx.Client(base_url='https://organizer.invalid',transport=httpx.MockTransport(lambda _:httpx.Response(status,text='SYNTHETIC_PRIVATE')))
    p=HostedProvider('test','secret','https://organizer.invalid','r1',client=client,sleep=lambda _:None)
    with pytest.raises(QueryFault,match=code) as e: p.generate_envelope('{}',m.OUTCOME_ADAPTER,'default_ir')
    assert 'SYNTHETIC_PRIVATE' not in str(e.value)

def test_missing_configuration_does_not_fallback():
    with pytest.raises(QueryFault,match='provider_configuration_error'): provider_from_environment({})

def test_cassette_replay_and_miss(tmp_path):
    path=tmp_path/'cassette.json';inner=ScriptedProvider([ir_for()])
    p=CassetteProvider(path,inner);first=p.generate_envelope('{}',m.OUTCOME_ADAPTER,'default_ir')
    replay=CassetteProvider(path);assert replay.generate_envelope('{}',m.OUTCOME_ADAPTER,'default_ir').output==first.output
    with pytest.raises(QueryFault,match='missing_cassette'): replay.generate_envelope('{"different":true}',m.OUTCOME_ADAPTER,'default_ir')
    assert 'SELECT ' not in path.read_text() and QUESTION not in path.read_text()
