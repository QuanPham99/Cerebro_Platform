import pytest
from cerebro import query_models as m
from cerebro.provenance import QueryFault,digest
from cerebro.grounding import trusted_scope
from cerebro.sql_compiler import validate_ir
from text2sql_factories import context,ir_for,QUESTION

@pytest.mark.parametrize('mutation',['unknown_table','orphan','cycle','duplicate','unknown_column'])
def test_structural_defects_fail_before_compile(mutation):
    _,_,s=context();ir=ir_for();nodes=list(ir.nodes)
    if mutation=='unknown_table': nodes[0]=m.ScanNode(node_id='s',table_id='table.private_table')
    if mutation=='orphan': nodes.append(m.ScanNode(node_id='orphan',table_id='table.customers'))
    if mutation=='cycle': nodes[-1]=nodes[-1].model_copy(update={'input_id':nodes[-1].node_id})
    if mutation=='duplicate': nodes.append(nodes[0])
    if mutation=='unknown_column':
        group=nodes[1].group_by[0].model_copy(update={'expression':m.ColumnExpression(ref=m.ColumnRef(table_id='table.customers',column='secret'))})
        nodes[1]=nodes[1].model_copy(update={'group_by':(group,)})
    with pytest.raises(QueryFault): validate_ir(ir.model_copy(update={'nodes':tuple(nodes)}),s,QUESTION)

def test_unauthorized_objects_never_enter_snapshot():
    resolver,_,_=context()
    scope=trusted_scope(resolver.bundle,tenant='limited',object_ids={'table.accounts'},classifications=('internal','public'))
    snapshot=resolver.resolve('customer names and account balance',scope)
    assert {o.object_id for o in snapshot.objects}<=scope.allowed_object_ids
    assert all(c.classification not in ('restricted','confidential') for o in snapshot.objects for c in o.columns)

def test_forged_scope_hash_is_rejected():
    resolver,scope,_=context()
    with pytest.raises(QueryFault,match='authorization_scope_integrity_error'):
        resolver.resolve(QUESTION,scope.model_copy(update={'authorization_scope_hash':'0'*64}))
