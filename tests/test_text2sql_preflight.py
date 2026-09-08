from importlib.metadata import version
from cerebro.preflight import check_preflight
from cerebro.cli import main
from text2sql_factories import seeded_db

def test_dependency_versions_are_exact():
    assert version('duckdb')=='1.5.5' and version('sqlglot')=='30.17.0'

def test_readiness_distinguishes_offline_from_missing_live_inputs(tmp_path):
    report=check_preflight(environ={})
    assert report.offline_ready and not report.live_prerequisites_ready
    assert {'missing_api_key','missing_model','missing_data_manifest','missing_provider_capability'}<={x.code for x in report.blockers}

def test_reference_cli_is_explicit_and_runs_without_key(tmp_path,capsys):
    db=seeded_db(tmp_path/'test.duckdb')
    assert main(['reference','--database',str(db),'--output',str(tmp_path/'reference.json')])==0
    assert 'offline_reference' in capsys.readouterr().out
