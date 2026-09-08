"""Hand-authored IR references for offline contract evidence, never live baseline."""
import json
from . import query_models as m
from .grounding import GroundingResolver,trusted_scope
from .provenance import digest
from .text2sql_provider import ProviderGeneration


def col(table,name): return m.ColumnExpression(ref=m.ColumnRef(table_id='table.'+table,column=name))
def out(node,alias): return m.OutputExpression(node_id=node,alias=alias)
def named(alias,expression): return m.NamedExpression(alias=alias,expression=expression)
def fn(name,*args): return m.FunctionExpression(function=name,arguments=args)
def metric(name): return m.MetricExpression(metric_id='metric.'+name)
def sort(expression): return m.SortKey(expression=expression,direction='asc',nulls='last')


def policy_literals():
    return (m.SnapshotGovernedLiteral(literal_id='literal.minimum-group-size',data_type='integer',value=5,source_object_id='policy.sensitive-banking-data'),
            m.SnapshotGovernedLiteral(literal_id='literal.calendar-month',data_type='string',value='month',source_object_id='dataset.bank-workshop'))


def reference_ir(question,snapshot):
    q=question.lower();nodes=[];group=[];measures=[]
    if 'gender' in q:
        nodes=[m.ScanNode(node_id='s',table_id='table.customers')];group=[named('gender',col('customers','gender'))];measures=[named('customer_count',fn('count'))]
    elif 'balance' in q:
        nodes=[m.ScanNode(node_id='s',table_id='table.accounts')];group=[named('account_type',col('accounts','account_type'))];measures=[named('average_balance',fn('avg',col('accounts','balance')))]
    elif 'fraud' in q:
        nodes=[m.ScanNode(node_id='s',table_id='table.card_transactions')]
        if 'card type' in q:
            nodes.extend([m.ScanNode(node_id='cards',table_id='table.cards'),m.JoinNode(node_id='joined',left_id='s',right_id='cards',relationship_id='relationship.card_transaction_card',join_type='inner')]);group=[named('card_type',col('cards','card_type'))]
        measures=[named('fraud_rate',metric('card-fraud-rate'))]
    elif 'late payment' in q:
        nodes=[m.ScanNode(node_id='s',table_id='table.loan_payments'),m.ScanNode(node_id='loans',table_id='table.loans'),m.JoinNode(node_id='joined',left_id='s',right_id='loans',relationship_id='relationship.loan_payment_loan',join_type='inner')]
        group=[named('loan_type',col('loans','loan_type'))];measures=[named('late_payment_rate',metric('late-payment-rate'))]
    elif 'transaction' in q and 'branch' in q:
        nodes=[m.ScanNode(node_id='s',table_id='table.transactions'),m.ScanNode(node_id='accounts',table_id='table.accounts'),m.JoinNode(node_id='j1',left_id='s',right_id='accounts',relationship_id='relationship.transaction_account',join_type='inner'),m.ScanNode(node_id='branches',table_id='table.branches'),m.JoinNode(node_id='joined',left_id='j1',right_id='branches',relationship_id='relationship.account_branch',join_type='inner')]
        group=[named('branch_name',col('branches','branch_name'))];measures=[named('total_amount',metric('transaction-volume'))]
    elif 'monthly transaction growth' in q:
        start=question.index('24');amount=m.QuestionLiteralRef(start=start,end=start+2,data_type='integer')
        nodes=[m.ScanNode(node_id='s',table_id='table.transactions'),m.FilterNode(node_id='recent',input_id='s',predicate=m.RelativeTimeExpression(date_column=m.ColumnRef(table_id='table.transactions',column='txn_date'),anchor='data_max',amount_ref=amount,unit='month',lower_inclusive=False,upper_inclusive=True))]
        group=[named('month',fn('date_trunc',m.LiteralExpression(ref=m.GovernedLiteralRef(literal_id='literal.calendar-month')),col('transactions','txn_date')))];measures=[named('volume',metric('transaction-volume'))]
    else:
        return m.ComplexQueryPlan(plan_version='008.complex-plan.v1',operator_ids=(),steps=(),expected_outputs=())
    nodes.append(m.AggregateNode(node_id='aggregate',input_id=nodes[-1].node_id,group_by=tuple(group),measures=tuple(measures),minimum_group_size=m.GovernedLiteralRef(literal_id='literal.minimum-group-size') if 'balance' in q else None))
    if 'monthly transaction growth' in q:
        nodes.append(m.WindowNode(node_id='previous',input_id='aggregate',outputs=(m.WindowExpression(alias='previous_volume',function='lag',argument=out('aggregate','volume'),order_by=(sort(out('aggregate','month')),)),)))
        delta=m.BinaryExpression(operator='subtract',left=out('aggregate','volume'),right=out('previous','previous_volume'))
        nodes.append(m.ProjectNode(node_id='project',input_id='previous',outputs=(named('month',out('aggregate','month')),named('volume',out('aggregate','volume')),named('growth_ratio',m.BinaryExpression(operator='divide',left=delta,right=out('previous','previous_volume'))))))
        nodes.append(m.SortNode(node_id='sorted',input_id='project',keys=(sort(out('project','month')),)))
    elif group:
        nodes.append(m.SortNode(node_id='sorted',input_id='aggregate',keys=tuple(sort(out('aggregate',g.alias)) for g in group)))
    used={n.table_id for n in nodes if n.kind=='scan'}|{n.relationship_id for n in nodes if n.kind=='join'}|{x.expression.metric_id for x in measures if x.expression.kind=='metric'}
    used|={obj.object_id for obj in snapshot.objects if obj.object_type in ('concept','policy') and set(obj.dependencies)&used}
    warnings=tuple(m.WarningDecision(object_id=w.object_id,warning_hash=w.warning_hash,control_id=w.control_id) for obj in snapshot.objects if obj.object_id in used for w in obj.warnings)
    return m.RelationalQueryIR(ir_version='008.ir.v1',root_node_id=nodes[-1].node_id,nodes=tuple(nodes),warning_decisions=warnings,assumptions=(m.Assumption(kind='snapshot',column=m.ColumnRef(table_id='table.accounts',column='balance'),operands=()),) if 'balance' in q else ())


class GoldenProvider:
    name='offline-reference';model='hand-authored-ir';model_revision='008.reference.v1';schema_mechanism='json_schema'
    def __init__(self): self.calls=[]
    def generate_envelope(self,envelope,adapter,mode,*,budget=None):
        self.calls.append((mode,envelope));request=json.loads(envelope)
        snapshot=m.GroundingSnapshot.model_validate(request['snapshot'])
        ir=reference_ir(request['question'],snapshot)
        if mode=='default_ir' and isinstance(ir,m.RelationalQueryIR) and any(n.kind=='window' for n in ir.nodes):
            ir=m.ComplexQueryPlan(plan_version='008.complex-plan.v1',operator_ids=('window.period_over_period.v1',),steps=(m.ComplexPlanStep(step_id='previous',operator_id='window.period_over_period.v1',input_object_ids=('table.transactions',),output_names=('previous_volume',)),),expected_outputs=('month','volume','growth_ratio'))
        return ProviderGeneration(adapter.validate_python(ir.model_dump()),input_tokens=len(envelope)//4,output_tokens=len(ir.model_dump_json())//4)
