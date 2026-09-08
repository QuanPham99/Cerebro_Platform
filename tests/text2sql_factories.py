"""Validated, value-free factories for the v3 acceptance matrix."""
from pathlib import Path
import duckdb
from cerebro import query_models as m
from cerebro.bundle import load_validated_bundle
from cerebro.paths import DEFAULT_BUNDLE
from cerebro.grounding import GroundingResolver,trusted_scope
from cerebro.prompting import GuardedProvider
from cerebro.reference import reference_ir,policy_literals,col,named,fn,out,sort
from cerebro.hosted_provider import ScriptedProvider
from cerebro.text2sql import Text2SQLAgent
from scripts.load_duckdb import create_schema

QUESTION='How many customers are there by gender?'

def context(question=QUESTION,objects=None):
    bundle=load_validated_bundle(DEFAULT_BUNDLE)
    scope=trusted_scope(bundle,tenant='tests',object_ids=objects)
    resolver=GroundingResolver(bundle,governed_literals=policy_literals())
    snapshot=resolver.resolve(question,scope)
    return resolver,scope,snapshot

def ir_for(question=QUESTION):
    resolver,scope,snapshot=context(question)
    return reference_ir(question,snapshot)

class FakeEngine:
    def __init__(self,result=None,fault=None):
        self.calls=[];self.fault=fault
        self.result=result or m.QueryResult(columns=('gender','customer_count'),column_types=('VARCHAR','BIGINT'),rows=(('F',2),),row_count=1,truncated=False,elapsed_ms=0)
    def explain(self,query,remaining_ms=None):
        self.calls.append('explain')
        if self.fault: raise self.fault
    def __call__(self,query,max_rows,remaining_ms=None): self.calls.append('execute');return self.result

def agent_for(outputs=None,engine=None,cache=None):
    resolver,scope,snapshot=context()
    provider=ScriptedProvider([ir_for()] if outputs is None else outputs)
    engine=engine or FakeEngine()
    agent=Text2SQLAgent(GuardedProvider(provider),resolver,engine,cache=cache)
    return agent,provider,engine,m.SQLGenerationRequest(question=QUESTION,authorization_scope=scope)

def seeded_db(path):
    create_schema(path,load_validated_bundle(DEFAULT_BUNDLE))
    con=duckdb.connect(str(path))
    con.execute("INSERT INTO customers (customer_id,gender,name) VALUES (1,'F','SYNTHETIC_ALICE'),(2,'F','SYNTHETIC_BETH'),(3,'M','SYNTHETIC_CHARLIE')")
    con.execute("INSERT INTO transactions (transaction_id,txn_date,amount) VALUES (1,'2026-01-15',100),(2,'2026-02-15',150),(3,'2026-03-15',120)")
    con.execute("INSERT INTO card_transactions (is_fraud) VALUES (1),(0),(0),(0)")
    con.close()
    return path


def with_warnings(ir,snapshot):
    return ir.model_copy(update={"warning_decisions":tuple(m.WarningDecision(object_id=w.object_id,warning_hash=w.warning_hash,control_id=w.control_id) for o in snapshot.objects for w in o.warnings)})
