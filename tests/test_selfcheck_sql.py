from dataclasses import replace
import pytest
from cerebro import query_models as m
from cerebro.provenance import QueryFault
from cerebro.sql_compiler import validate_ir,DialectCompiler,authorize_compiled
from text2sql_factories import context,ir_for,QUESTION,col,named,fn,with_warnings

@pytest.mark.parametrize('sql',["SELECT secret FROM private_table","SELECT * FROM read_csv('/tmp/private.csv')","SELECT * FROM customers","SELECT list(name) FROM customers","SELECT MIN(name) FROM customers","SELECT 1; SELECT 2","WITH unused AS (SELECT COUNT(*) FROM customers) SELECT 999"])
def test_compiler_defects_cannot_inject_sql(sql):
    _,_,s=context();v=validate_ir(ir_for(),s,QUESTION);c=DialectCompiler().compile(v,s,QUESTION)
    bad=replace(c,query=c.query.model_copy(update={'sql':sql}))
    with pytest.raises(QueryFault,match='compiled_sql_containment_error'): authorize_compiled(bad,v,s,QUESTION)

def test_sensitive_name_requires_explicit_disclosure_and_final_limit():
    _,_,s=context();ir=m.RelationalQueryIR(ir_version='008.ir.v1',root_node_id='p',nodes=(m.ScanNode(node_id='s',table_id='table.customers'),m.ProjectNode(node_id='p',input_id='s',outputs=(named('name',col('customers','name')),))))
    with pytest.raises(QueryFault,match='policy_disallowed'): validate_ir(with_warnings(ir,s),s,QUESTION)

def test_collection_aggregate_is_not_in_ir_allowlist():
    from pydantic import ValidationError
    with pytest.raises(ValidationError): m.FunctionExpression(function='list',arguments=(col('customers','name'),))

def test_raw_metric_wrapper_fails():
    from cerebro.reference import reference_ir
    q='What percentage of card transactions are fraud?';_,_,s=context(q);ir=reference_ir(q,s)
    n=ir.nodes[-1];expr=m.FunctionExpression(function='coalesce',arguments=(n.measures[0].expression,n.measures[0].expression))
    bad=n.model_copy(update={'measures':(named('fraud_rate',expr),)})
    with pytest.raises(QueryFault,match='formula_not_verbatim'): validate_ir(ir.model_copy(update={'nodes':(*ir.nodes[:-1],bad)}),s,q)
