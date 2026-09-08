"""Organizer gateway, explicit offline adapters, and sanitized transport faults."""
from __future__ import annotations
import json
import os
import time
from decimal import Decimal
from pathlib import Path
import httpx
from pydantic import ValidationError
from .provenance import QueryFault,atomic_json,digest
from .text2sql_provider import ProviderGeneration

RETRYABLE_STATUS=frozenset({408,409,425,429,500,502,503,504})

class ProviderUnavailable(QueryFault):
    def __init__(self,attempts=0): super().__init__('provider_unavailable',transport_attempts=attempts)

class HostedProvider:
    name='organizer'
    schema_mechanism='json_schema'

    def __init__(self,model,api_key,base_url,model_revision,client=None,timeout=20.0,
                 max_transport_attempts=2,backoff_base=0.5,sleep=time.sleep,
                 input_price_per_million='0',output_price_per_million='0'):
        if not model or not api_key or not base_url or not model_revision:
            raise QueryFault('provider_configuration_error')
        if not 1<=max_transport_attempts<=2 or not 0<timeout<=20:
            raise QueryFault('provider_configuration_error')
        self.model,self.model_revision=model,model_revision
        self._key=api_key; self.client=client or httpx.Client(base_url=base_url,timeout=timeout)
        self.timeout=timeout; self.max_transport_attempts=max_transport_attempts
        self.backoff_base,self.sleep=backoff_base,sleep
        self.input_price=Decimal(input_price_per_million);self.output_price=Decimal(output_price_per_million)

    def __repr__(self): return f'HostedProvider(model={self.model!r})'

    def generate_envelope(self,envelope,adapter,mode,*,budget=None):
        payload={'model':self.model,'temperature':0,'max_tokens':8000,
                 'messages':[{'role':'system','content':'Return the requested typed relational IR outcome. Use exact supplied identifiers and literal references. Never emit SQL or physical literal values.'},
                             {'role':'user','content':envelope}],
                 'response_format':{'type':'json_schema','json_schema':{'name':'query_outcome','strict':True,'schema':adapter.json_schema()}}}
        if budget is not None:
            payload['max_tokens']=min(8000,budget.limits.max_output_tokens-budget.output)
        recovered=[]
        for attempt in range(1,self.max_transport_attempts+1):
            if budget is not None:
                budget.check()
                budget.record('transport','started')
            timeout=min(self.timeout,budget.remaining_ms/1000) if budget is not None else self.timeout
            try:
                response=self.client.post('/chat/completions',json=payload,headers={'authorization':f'Bearer {self._key}'},timeout=timeout)
            except (httpx.TransportError,httpx.TimeoutException):
                recovered.append('provider_transport_error')
            else:
                if response.status_code in RETRYABLE_STATUS:
                    recovered.append('provider_retryable_status')
                elif response.status_code>=400:
                    raise QueryFault('provider_rejected',transport_attempts=attempt) from None
                else:
                    try:
                        body=response.json()
                        output=adapter.validate_json(body['choices'][0]['message']['content'])
                        usage=body.get('usage',{})
                        inp=max(0,int(usage.get('prompt_tokens',len(envelope))))
                        out=max(0,int(usage.get('completion_tokens',len(body['choices'][0]['message']['content']))))
                    except (ValueError,ValidationError,KeyError,IndexError,TypeError):
                        raise QueryFault('unparsable_fallback_ir' if mode=='planned_ir' else 'unparsable_generation_outcome',transport_attempts=attempt) from None
                    cost=(self.input_price*inp+self.output_price*out)/Decimal(1000000)
                    return ProviderGeneration(output,attempt,inp,out,str(cost),tuple(recovered))
            if attempt<self.max_transport_attempts:
                self.sleep(self.backoff_base*2**(attempt-1))
        raise ProviderUnavailable(self.max_transport_attempts)

class ScriptedProvider:
    name='scripted'
    model='offline-reference'
    model_revision='008.reference.v1'
    schema_mechanism='json_schema'
    def __init__(self,responses):
        self.responses=list(responses);self.calls=[]
    def generate_envelope(self,envelope,adapter,mode,*,budget=None):
        self.calls.append((mode,envelope))
        if not self.responses: raise QueryFault('missing_cassette')
        value=self.responses.pop(0)
        if isinstance(value,Exception): raise value
        if callable(value): value=value(json.loads(envelope))
        try:
            parsed=adapter.validate_python(value.model_dump() if hasattr(value,'model_dump') else value)
        except ValidationError:
            raise QueryFault('unparsable_fallback_ir' if mode=='planned_ir' else 'unparsable_generation_outcome') from None
        return ProviderGeneration(parsed,input_tokens=len(envelope)//4,output_tokens=len(parsed.model_dump_json())//4)

class CassetteProvider:
    name='cassette'
    model='offline-reference'
    model_revision='008.cassette.v1'
    schema_mechanism='json_schema'
    def __init__(self,path,inner=None):
        self.path=Path(path); self.inner=inner
        self.entries=json.loads(self.path.read_text()) if self.path.exists() else {}
    def generate_envelope(self,envelope,adapter,mode,*,budget=None):
        key=digest({'envelope':envelope,'mode':mode,'schema':adapter.json_schema()})
        if key in self.entries:
            return ProviderGeneration(adapter.validate_python(self.entries[key]))
        if self.inner is None: raise QueryFault('missing_cassette')
        result=self.inner.generate_envelope(envelope,adapter,mode,budget=budget)
        # Decode to the caller's strict value-free schema before retaining output.
        clean=adapter.validate_python(result.output.model_dump()).model_dump(mode='json')
        self.entries[key]=clean; atomic_json(self.path,self.entries)
        return result


def provider_from_environment(environ=None):
    env=os.environ if environ is None else environ
    required=['CEREBRO_API_KEY','CEREBRO_MODEL','CEREBRO_BASE_URL','CEREBRO_MODEL_REVISION']
    if any(not env.get(k) for k in required): raise QueryFault('provider_configuration_error')
    return HostedProvider(env['CEREBRO_MODEL'],env['CEREBRO_API_KEY'],env['CEREBRO_BASE_URL'],env['CEREBRO_MODEL_REVISION'],
                          input_price_per_million=env.get('CEREBRO_INPUT_PRICE_PER_MILLION','0'),output_price_per_million=env.get('CEREBRO_OUTPUT_PRICE_PER_MILLION','0'))
