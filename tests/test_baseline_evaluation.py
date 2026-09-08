import pytest
from cerebro.evaluation import run_offline_reference,run_sql_baseline
from cerebro.provenance import QueryFault
from text2sql_factories import seeded_db

def test_nonvacuous_offline_reference_is_not_live_baseline(tmp_path):
    db=seeded_db(tmp_path/'test.duckdb');path=tmp_path/'reference.json'
    report=run_offline_reference(database_path=db,artifact_path=path)
    assert report['run_kind']=='offline_reference' and len(report['questions'])==10
    assert report['totals']['ok']>=7
    growth=next(q for q in report['questions'] if q['id']=='GQ-08')
    assert growth['status']=='ok' and growth['evidence']['generation_route']=='planned_ir'
    assert 'SYNTHETIC_ALICE' not in path.read_text() and '"question":' not in path.read_text()

def test_missing_live_evidence_never_overwrites_artifact(tmp_path):
    path=tmp_path/'baseline.json';path.write_text('previous-evidence')
    with pytest.raises(QueryFault,match='live_baseline_blocked'): run_sql_baseline(artifact_path=path)
    assert path.read_text()=='previous-evidence'
