from cerebro.evaluation import build_agent
from cerebro.reference import GoldenProvider
from cerebro import query_models as m
from text2sql_factories import context,seeded_db

def test_monthly_growth_executes_aggregate_window_project(tmp_path):
    db=seeded_db(tmp_path/'test.duckdb');resolver,scope,_=context()
    provider=GoldenProvider();agent,engine=build_agent(db,provider,resolver.bundle)
    try:
        result=agent.run(m.SQLGenerationRequest(question='What is monthly transaction growth for the last 24 months?',authorization_scope=scope))
        assert result.status=='ok',result
        assert result.generation_route=='planned_ir' and len(provider.calls)==2
        rows=result.result.rows
        assert [r[1] for r in rows]==[100,150,120]
        assert [r[2] for r in rows]==[None,0.5,-0.2]
        assert result.sql_artifact.parameter_count>0
        serialized=result.model_dump_json(exclude={'result'})
        assert '2026-01-15' not in serialized and '"value"' not in serialized
    finally: engine.close()
