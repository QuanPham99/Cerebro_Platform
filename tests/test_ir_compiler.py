"""Typed/literal/lineage mutations stop before engine contact (T-705–719)."""
from dataclasses import replace
from datetime import date
import hashlib
import json
import os
import subprocess
import pytest
from pydantic import ValidationError
from cerebro import query_models as m
from cerebro.sql_compiler import validate_ir,DialectCompiler,authorize_compiled
from cerebro.provenance import QueryFault,digest,canonical_question_sha256
from cerebro.literals import resolve_literal
from cerebro.reference import reference_ir
from text2sql_factories import context,ir_for,col,out,named,fn,sort,with_warnings,QUESTION


def test_canonicalization_and_question_hash_are_exact():
    raw='  Cafe\u0301\t transactions\n24\u00a0months  '
    canonical=m.canonicalize_question(raw)
    assert canonical=='Café transactions 24 months'
    assert canonical_question_sha256(canonical)==hashlib.sha256(canonical.encode()).hexdigest()


def test_scope_hash_stable_across_process_hash_seeds():
    code="from cerebro.provenance import digest; print(digest({'objects':frozenset({'table.accounts','table.customers','table.loans'})}))"
    outputs=[subprocess.check_output([os.path.abspath('.venv/bin/python'),'-c',code],env={**os.environ,'PYTHONHASHSEED':str(seed),'PYTHONPATH':'src'},text=True).strip() for seed in (1,5,42)]
    assert len(set(outputs))==1

@pytest.mark.parametrize('token,typ,expected',[('24','integer',24),('twenty','integer',20),('-1.25','decimal','-1.25'),('TRUE','boolean',True),('2024-02-29','date','2024-02-29')])
def test_literal_resolution_is_exact_and_local(token,typ,expected):
    _,_,s=context();ref=m.QuestionLiteralRef(start=0,end=len(token),data_type=typ)
    value=resolve_literal(ref,token,s)
    assert str(value)==str(expected)
    serialized=m.BoundParameter(position=1,data_type=typ,value=value).model_dump_json()
    assert '"value"' not in serialized

@pytest.mark.parametrize('token,typ',[('1e3','decimal'),('nan','decimal'),('2025-02-29','date'),('maybe','boolean'),('24months','integer')])
def test_invalid_literal_does_not_coerce(token,typ):
    _,_,s=context()
    with pytest.raises(QueryFault): resolve_literal(m.QuestionLiteralRef(start=0,end=len(token),data_type=typ),token,s)


def test_partial_token_span_is_rejected():
    _,_,s=context()
    with pytest.raises(QueryFault,match='invalid_question_span'): resolve_literal(m.QuestionLiteralRef(start=0,end=2,data_type='integer'),'240',s)

@pytest.mark.parametrize('kind',['non_boolean_filter','unavailable_output','duplicate_output_alias','invalid_aggregate_placement','invalid_function_signature'])
def test_type_mutations_fail_locally(kind):
    _,_,s=context();nodes=[m.ScanNode(node_id='s',table_id='table.customers')]
    if kind=='non_boolean_filter': nodes.append(m.FilterNode(node_id='bad',input_id='s',predicate=col('customers','gender')))
    elif kind=='unavailable_output': nodes.append(m.ProjectNode(node_id='bad',input_id='s',outputs=(named('x',out('missing','value')),)))
    elif kind=='duplicate_output_alias': nodes.append(m.ProjectNode(node_id='bad',input_id='s',outputs=(named('x',col('customers','gender')),named('x',col('customers','gender')))))
    elif kind=='invalid_aggregate_placement': nodes.append(m.FilterNode(node_id='bad',input_id='s',predicate=fn('count')))
    else: nodes.append(m.AggregateNode(node_id='bad',input_id='s',group_by=(),measures=(named('x',fn('sum',col('customers','name'))),)))
    ir=with_warnings(m.RelationalQueryIR(ir_version='008.ir.v1',root_node_id='bad',nodes=tuple(nodes)),s)
    with pytest.raises(QueryFault,match=kind): validate_ir(ir,s,QUESTION)


def test_sensitive_case_propagates_condition_lineage():
    q='Show 1 or 2 based on customer income';_,_,s=context(q)
    lit=lambda digit:m.LiteralExpression(ref=m.QuestionLiteralRef(start=q.index(digit),end=q.index(digit)+1,data_type='integer'))
    predicate=m.BinaryExpression(operator='gt',left=col('customers','annual_income'),right=lit('1'))
    expression=m.CaseExpression(branches=(m.WhenThen(when=predicate,then=lit('1')),),else_expression=lit('2'))
    ir=with_warnings(m.RelationalQueryIR(ir_version='008.ir.v1',root_node_id='p',nodes=(m.ScanNode(node_id='s',table_id='table.customers'),m.ProjectNode(node_id='p',input_id='s',outputs=(named('flag',expression),)))),s)
    with pytest.raises(QueryFault,match='policy_disallowed'): validate_ir(ir,s,q)


def test_sensitive_top_five_has_complete_lineage_disclosure():
    q='Show top 5 customer names';_,_,s=context(q)
    ref=m.ColumnRef(table_id='table.customers',column='name')
    ir=with_warnings(m.RelationalQueryIR(ir_version='008.ir.v1',root_node_id='limit',nodes=(m.ScanNode(node_id='s',table_id='table.customers'),m.ProjectNode(node_id='p',input_id='s',outputs=(named('name',m.ColumnExpression(ref=ref)),)),m.LimitNode(node_id='limit',input_id='p',count=m.QuestionLiteralRef(start=q.index('5'),end=q.index('5')+1,data_type='integer'))),requested_disclosures=(m.RequestedDisclosure(sources=(ref,)),)),s)
    c=DialectCompiler().compile(validate_ir(ir,s,q),s,q)
    assert c.disclosures[0].row_bound==5 and c.lineage[0].sources==(ref,)
    assert c.query.parameters[0].value==5
    assert 'SYNTHETIC' not in c.query.model_dump_json()


def test_compiler_rejects_validated_ir_mutation():
    _,_,s=context();v=validate_ir(ir_for(),s,QUESTION)
    forged=v.model_copy(update={'ir_hash':'0'*64})
    with pytest.raises(QueryFault,match='validated_ir_integrity_error'): DialectCompiler().compile(forged,s,QUESTION)


def test_parameter_mutation_cannot_pass_containment():
    q='What is monthly transaction growth for the last 24 months?';_,_,s=context(q);ir=reference_ir(q,s)
    valid=validate_ir(ir,s,q,'planned_ir','a'*64);c=DialectCompiler().compile(valid,s,q)
    params=list(c.query.parameters);params[-1]=params[-1].model_copy(update={'value':999})
    bad=replace(c,query=c.query.model_copy(update={'parameters':tuple(params)}))
    with pytest.raises(QueryFault,match='compiled_sql_containment_error'): authorize_compiled(bad,valid,s,q)


def set_fixture(snapshot):
    nodes=[]
    for suffix in ('left','right'):
        nodes.extend((m.ScanNode(node_id='s_'+suffix,table_id='table.customers'),m.AggregateNode(node_id=suffix,input_id='s_'+suffix,group_by=(named('gender',col('customers','gender')),),measures=(named('n',fn('count')),))))
    nodes.append(m.SetOperationNode(node_id='combined',left_id='left',right_id='right',operator='union',all=True))
    return with_warnings(m.RelationalQueryIR(ir_version='008.ir.v1',root_node_id='combined',nodes=tuple(nodes)),snapshot)


def test_guarded_set_operation_is_supported_but_default_route_is_not():
    _,_,s=context();ir=set_fixture(s)
    with pytest.raises(QueryFault,match='unsupported_complexity'): validate_ir(ir,s,QUESTION)
    c=DialectCompiler().compile(validate_ir(ir,s,QUESTION,'planned_ir','a'*64),s,QUESTION)
    assert 'UNION ALL' in c.query.sql and len(c.lineage)==2

@pytest.mark.parametrize('mutation',['arity','type','unsafe_leaf'])
def test_set_operation_checks_every_leaf(mutation):
    _,_,s=context();ir=set_fixture(s);nodes=list(ir.nodes)
    if mutation=='arity': nodes[3]=nodes[3].model_copy(update={'group_by':()})
    elif mutation=='type': nodes[3]=nodes[3].model_copy(update={'group_by':(named('gender',col('customers','customer_id')), )})
    else: nodes[2]=m.ScanNode(node_id='s_right',table_id='table.private_table')
    with pytest.raises(QueryFault): validate_ir(ir.model_copy(update={'nodes':tuple(nodes)}),s,QUESTION,'planned_ir','a'*64)
