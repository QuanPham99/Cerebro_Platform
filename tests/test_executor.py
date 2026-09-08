import pytest
from cerebro import query_models as m
from cerebro.executor import DuckDBExecutor
from cerebro.provenance import QueryFault
from cerebro.sql_compiler import validate_ir,DialectCompiler
from text2sql_factories import context,ir_for,QUESTION,seeded_db


def test_real_readonly_execution_and_metadata(tmp_path):
    db=seeded_db(tmp_path/'test.duckdb');_,_,s=context();v=validate_ir(ir_for(),s,QUESTION);query=DialectCompiler().compile(v,s,QUESTION).query
    ex=DuckDBExecutor(db)
    try:
        ex.explain(query);result=ex(query,100)
        assert result.rows==( ('F',2),('M',1))
        assert result.column_types==('VARCHAR','BIGINT')
        with pytest.raises(QueryFault,match='uncompiled_query'): ex('SELECT 1',10)
        import duckdb
        with pytest.raises(duckdb.Error): ex.connection.execute('CREATE TABLE forbidden(x INT)')
        with pytest.raises(duckdb.Error): ex.connection.execute("SELECT * FROM read_csv('/tmp/nonexistent.csv')")
    finally: ex.close()

def test_fetch_cap_discards_extra_row(tmp_path):
    db=seeded_db(tmp_path/'test.duckdb');_,_,s=context();v=validate_ir(ir_for(),s,QUESTION,max_rows=1);query=DialectCompiler().compile(v,s,QUESTION,1).query
    ex=DuckDBExecutor(db)
    try:
        result=ex(query,1)
        assert result.row_count==1 and result.truncated and len(result.rows)==1
    finally: ex.close()

def test_deadline_exhaustion_prevents_contact(tmp_path):
    db=seeded_db(tmp_path/'test.duckdb');_,_,s=context();query=DialectCompiler().compile(validate_ir(ir_for(),s,QUESTION),s,QUESTION).query
    ex=DuckDBExecutor(db)
    try:
        with pytest.raises(QueryFault,match='budget_exceeded'): ex.explain(query,remaining_ms=0)
    finally: ex.close()


def test_watchdog_interrupts_and_is_joined_before_next_query(tmp_path):
    from cerebro.executor import QueryTimeout
    db=seeded_db(tmp_path/'timeout.duckdb')
    executor=DuckDBExecutor(db,timeout_seconds=0.05)
    huge=m.CompiledQuery(sql='SELECT COUNT(*) FROM range(100000000000) a, range(1000000) b',parameters=(),ir_hash='a'*64,compiler_version='test',dialect='duckdb')
    small=huge.model_copy(update={'sql':'SELECT 1 AS n'})
    try:
        with pytest.raises(QueryTimeout,match='execution_timeout'): executor(huge,10)
        assert executor(small,10).rows==((1,),)
    finally: executor.close()


def test_explain_failure_is_sanitized(tmp_path):
    db=seeded_db(tmp_path/'binding.duckdb')
    executor=DuckDBExecutor(db)
    bad=m.CompiledQuery(sql='SELECT SYNTHETIC_PRIVATE_COLUMN FROM customers',parameters=(),ir_hash='a'*64,compiler_version='test',dialect='duckdb')
    try:
        with pytest.raises(QueryFault,match='explain_failed') as error: executor.explain(bad)
        assert 'SYNTHETIC_PRIVATE_COLUMN' not in str(error.value)
    finally: executor.close()
