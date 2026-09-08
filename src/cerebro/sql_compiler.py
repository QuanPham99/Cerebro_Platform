"""Typed relational compiler and independent post-compile containment gate.

Every identifier is constructed with sqlglot. No caller/model SQL is accepted.
FR-709–715, AC-700–706, AC-716.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections import Counter

import sqlglot
from sqlglot import exp

from . import query_models as m
from .grounding import verify_snapshot
from .literals import resolve_literal
from .provenance import QueryFault, digest, canonical_question_sha256

COMPILER_VERSION = '008.compiler.v1'
NUMERIC = {'integer', 'decimal'}
TIERS = ['public', 'internal', 'confidential', 'restricted']
AGGREGATES = {'count', 'sum', 'avg', 'min', 'max', 'stddev', 'variance'}
BINARY = {'eq': exp.EQ, 'neq': exp.NEQ, 'lt': exp.LT, 'lte': exp.LTE,
          'gt': exp.GT, 'gte': exp.GTE, 'and': exp.And, 'or': exp.Or,
          'add': exp.Add, 'subtract': exp.Sub, 'multiply': exp.Mul, 'divide': exp.Div}


def fail(code, subject=''):
    raise QueryFault(code, subject)


@dataclass(frozen=True)
class Slot:
    alias: str
    data_type: str
    sources: tuple[m.ColumnRef, ...] = ()
    classification: str = 'internal'
    reducing: bool = False
    metrics: tuple[str, ...] = ()
    origins: frozenset[tuple[str, str]] = frozenset()
    physical: m.ColumnRef | None = None
    aggregated: bool = False


def merged(slots, typ, *, reducing=False, aggregated=False):
    return Slot('', typ, tuple(sorted({s for x in slots for s in x.sources}, key=lambda x: (x.table_id, x.column))),
                max((x.classification for x in slots), key=TIERS.index, default='public'), reducing,
                tuple(sorted({v for x in slots for v in x.metrics})), aggregated=aggregated)


@dataclass(frozen=True)
class Compilation:
    query: m.CompiledQuery
    lineage: tuple[m.OutputLineage, ...]
    disclosures: tuple[m.DisclosureRecord, ...]
    usage: m.GroundingUsage


class _Builder:
    def __init__(self, ir, snapshot, question, route, max_rows, *, resolve):
        self.ir, self.snapshot, self.question, self.route = ir, snapshot, question, route
        self.max_rows, self.resolve = max_rows, resolve
        self.nodes = {n.node_id: n for n in ir.nodes}
        if not 1 <= max_rows <= 1000:
            fail('invalid_row_cap')
        if len(self.nodes) != len(ir.nodes) or not 1 <= len(ir.nodes) <= 128:
            fail('invalid_ir_shape')
        self.objects = {x.object_id: x for x in snapshot.objects}
        self.columns = {c.ref: c for x in snapshot.objects for c in x.columns}
        self.relationships = {r.relationship_id: r for x in snapshot.objects for r in x.relationships}
        self.built, self.visiting, self.ctes, self.pending = {}, set(), [], []
        self.relation_aggregated = {}
        self.used, self.metric_ids, self.limits = set(), set(), []
        self.aggregate_guards = []

    def parameter(self, ref=None, *, value=None, typ=None):
        # Resolve only after the complete shape/type pass has succeeded.
        index = len(self.pending)
        if ref is not None:
            if ref.kind == 'question':
                typ = ref.data_type
            else:
                literal = next((x for x in self.snapshot.governed_literals if x.literal_id == ref.literal_id), None)
                if literal is None:
                    fail('ungrounded_governed_literal')
                typ = literal.data_type
        self.pending.append((ref, value, typ))
        return exp.Placeholder(this=f'p{index}'), Slot('', typ or 'integer', classification='public')

    @staticmethod
    def physical(ref, slots):
        matches = [x for x in slots if x.physical == ref]
        if len(matches) != 1:
            fail('unavailable_column', ref.table_id)
        return matches[0]

    def expression(self, expression, slots, placement='scalar', depth=0):
        if depth > 32:
            fail('expression_depth_exceeded')
        kind = expression.kind
        if kind == 'column':
            if placement == 'measure': fail('invalid_aggregate_placement')
            if expression.ref not in self.columns:
                fail('unknown_column', expression.ref.table_id)
            slot = self.physical(expression.ref, slots)
            return exp.column(slot.alias, quoted=True), slot
        if kind == 'output':
            found = [x for x in slots if (expression.node_id, expression.alias) in x.origins]
            if len(found) != 1:
                fail('unavailable_output')
            return exp.column(found[0].alias, quoted=True), found[0]
        if kind == 'literal':
            return self.parameter(expression.ref)
        if kind == 'metric':
            if placement != 'measure':
                fail('invalid_aggregate_placement')
            obj = self.objects.get(expression.metric_id)
            if obj is None or obj.object_type != 'metric' or not obj.formula:
                fail('unknown_metric')
            self.used.add(obj.object_id)
            self.metric_ids.add(obj.object_id)
            tree = sqlglot.parse_one(obj.formula, read='duckdb')
            sources = []
            for column in list(tree.find_all(exp.Column)):
                ref = m.ColumnRef(table_id=f'table.{column.table}', column=column.name)
                slot = self.physical(ref, slots)
                sources.append(slot)
                column.replace(exp.column(slot.alias, quoted=True))
            if tree.find(exp.Select) or tree.find(exp.Table):
                fail('invalid_metric_formula')
            for literal in list(tree.find_all(exp.Literal)):
                val = literal.this if literal.is_string else float(literal.this) if '.' in literal.this else int(literal.this)
                node, _ = self.parameter(value=val, typ='string' if literal.is_string else 'decimal' if isinstance(val, float) else 'integer')
                literal.replace(node)
            info = merged(sources, obj.metric_result_type, reducing=True, aggregated=True)
            return tree, replace(info, metrics=(obj.object_id,))
        if kind == 'relative_time':
            date_slot = self.physical(expression.date_column, slots)
            if date_slot.data_type not in ('date', 'timestamp'):
                fail('invalid_function_signature')
            amount, typ = self.parameter(expression.amount_ref)
            if typ.data_type != 'integer':
                fail('literal_type_mismatch')
            self.aggregate_guards.append(('positive', expression.amount_ref))
            physical = exp.column(expression.date_column.column, quoted=True)
            anchor = exp.select(exp.Max(this=physical)).from_(exp.to_table(expression.date_column.table_id[6:], quoted=True)).subquery()
            interval = exp.Mul(this=amount, expression=exp.Interval(this=exp.Literal.string('1'), unit=exp.Var(this=expression.unit.upper())))
            current = exp.column(date_slot.alias, quoted=True)
            lower = (exp.GTE if expression.lower_inclusive else exp.GT)(this=current.copy(), expression=exp.Sub(this=anchor.copy(), expression=interval))
            upper = (exp.LTE if expression.upper_inclusive else exp.LT)(this=current.copy(), expression=anchor.copy())
            return exp.and_(lower, upper), merged([date_slot], 'boolean')
        if kind == 'function':
            fn = expression.function
            aggregate = fn in AGGREGATES
            if aggregate and placement != 'measure':
                fail('invalid_aggregate_placement' if placement != 'inside_aggregate' else 'nested_aggregate_or_window')
            args = [self.expression(a, slots, 'inside_aggregate' if aggregate else placement, depth+1) for a in expression.arguments]
            trees, infos = [x[0] for x in args], [x[1] for x in args]
            if fn == 'count' and not trees:
                trees = [exp.Star()]
            elif fn in AGGREGATES and len(trees) != 1:
                fail('invalid_function_signature')
            elif fn in ('nullif', 'date_trunc') and len(trees) != 2:
                fail('invalid_function_signature')
            elif fn == 'coalesce' and len(trees) < 2:
                fail('invalid_function_signature')
            if fn in ('sum', 'avg', 'stddev', 'variance') and infos[0].data_type not in NUMERIC:
                fail('invalid_function_signature')
            if fn == 'date_trunc' and (infos[0].data_type != 'string' or infos[1].data_type not in ('date', 'timestamp')):
                fail('invalid_function_signature')
            if fn in ('nullif', 'coalesce') and len({x.data_type for x in infos}) > 1 and not {x.data_type for x in infos} <= NUMERIC:
                fail('invalid_function_signature')
            reducing = fn in ('count', 'sum', 'avg', 'stddev', 'variance')
            typ = 'integer' if fn == 'count' else 'timestamp' if fn == 'date_trunc' else 'decimal' if fn in ('avg','stddev','variance') else infos[0].data_type
            info = merged(infos, typ, reducing=reducing or (bool(infos) and all(x.reducing for x in infos)), aggregated=aggregate or any(x.aggregated for x in infos))
            return exp.func(fn.upper(), *trees), info
        if kind == 'binary':
            left, li = self.expression(expression.left, slots, placement, depth+1)
            right, ri = self.expression(expression.right, slots, placement, depth+1)
            op = expression.operator
            if op in ('and','or'):
                if li.data_type != 'boolean' or ri.data_type != 'boolean': fail('non_boolean_filter')
                typ = 'boolean'
            elif op in ('add','subtract','multiply','divide'):
                if li.data_type not in NUMERIC or ri.data_type not in NUMERIC: fail('invalid_operand_type')
                typ = 'decimal' if op == 'divide' or 'decimal' in (li.data_type,ri.data_type) else 'integer'
                if op == 'divide': right = exp.Nullif(this=right, expression=exp.Literal.number(0))
            else:
                if li.data_type != ri.data_type and not {li.data_type,ri.data_type} <= NUMERIC: fail('invalid_operand_type')
                typ = 'boolean'
            return BINARY[op](this=exp.Paren(this=left), expression=exp.Paren(this=right)), merged([li,ri],typ,reducing=all(x.reducing or not x.sources for x in (li,ri)),aggregated=li.aggregated or ri.aggregated)
        if kind == 'in':
            tree, info = self.expression(expression.expression, slots, placement, depth+1)
            args = [self.expression(v, slots, placement, depth+1) for v in expression.values]
            if any(x[1].data_type != info.data_type for x in args): fail('invalid_operand_type')
            result = exp.In(this=tree, expressions=[a[0] for a in args])
            return (exp.Not(this=result) if expression.negated else result), replace(info,data_type='boolean')
        if kind == 'case':
            conditions, infos, predicate_infos = [], [], []
            for branch in expression.branches:
                test, ti = self.expression(branch.when,slots,placement,depth+1)
                then, vi = self.expression(branch.then,slots,placement,depth+1)
                if ti.data_type != 'boolean': fail('non_boolean_filter')
                conditions.append(exp.If(this=test,true=then)); infos.append(vi); predicate_infos.append(ti)
            default, info = self.expression(expression.else_expression,slots,placement,depth+1)
            infos.append(info)
            if len({x.data_type for x in infos}) != 1: fail('invalid_operand_type')
            return exp.Case(ifs=conditions,default=default), merged(infos+predicate_infos,info.data_type,aggregated=any(x.aggregated for x in infos))
        fail('unsupported_expression')

    def named(self, expressions, inputs, node_id, placement):
        if not expressions or len({x.alias for x in expressions}) != len(expressions):
            fail('duplicate_output_alias')
        trees, slots = [], []
        for item in expressions:
            if item.expression.kind != 'metric' and '"kind":"metric"' in item.expression.model_dump_json():
                fail('formula_not_verbatim')
            tree, info = self.expression(item.expression, inputs, placement)
            trees.append(exp.alias_(tree,item.alias,quoted=True))
            direct = info.physical if item.expression.kind == 'column' else None
            slots.append(replace(info,alias=item.alias,origins=frozenset({(node_id,item.alias)}),physical=direct))
        return trees, slots

    def child(self, node_id):
        self.build(node_id)
        return self.built[node_id]

    def build(self, node_id):
        if node_id in self.built: return
        if node_id not in self.nodes or node_id in self.visiting: fail('invalid_ir_shape')
        self.visiting.add(node_id)
        n = self.nodes[node_id]
        if n.kind == 'scan':
            table = self.objects.get(n.table_id)
            if table is None or table.object_type != 'table': fail('unknown_table')
            self.used.add(n.table_id)
            slots = [Slot(f'{n.node_id}__{c.ref.column}', c.data_type,(c.ref,),c.classification,
                          physical=c.ref,origins=frozenset({(node_id,f'{n.node_id}__{c.ref.column}')})) for c in table.columns]
            if not slots: fail('no_authorized_columns')
            tree = exp.select(*[exp.alias_(exp.column(c.ref.column,quoted=True),slot.alias,quoted=True) for c,slot in zip(table.columns,slots)]).from_(exp.to_table(n.table_id[6:],quoted=True))
        elif n.kind == 'join':
            left, right = self.child(n.left_id), self.child(n.right_id)
            slots = left+right
            if len({x.alias for x in slots}) != len(slots): fail('duplicate_output_alias')
            rel = self.relationships.get(n.relationship_id)
            if rel is None: fail('undeclared_join')
            self.used.add(rel.relationship_id)
            try:
                a,b = self.physical(rel.left,left),self.physical(rel.right,right)
            except QueryFault:
                a,b = self.physical(rel.right,left),self.physical(rel.left,right)
            tree = exp.select(*[exp.column(s.alias,quoted=True) for s in slots]).from_(exp.to_table(n.left_id,quoted=True)).join(exp.to_table(n.right_id,quoted=True),on=exp.EQ(this=exp.column(a.alias,quoted=True),expression=exp.column(b.alias,quoted=True)),join_type=n.join_type)
        elif n.kind == 'set_operation':
            if self.route != 'planned_ir': fail('unsupported_complexity')
            left,right = self.child(n.left_id),self.child(n.right_id)
            if len(left)!=len(right): fail('set_output_arity_mismatch')
            slots=[]
            for a,b in zip(left,right):
                if a.data_type!=b.data_type: fail('set_output_type_mismatch')
                slots.append(replace(merged([a,b],a.data_type,reducing=a.reducing and b.reducing,aggregated=a.aggregated and b.aggregated),alias=a.alias,origins=frozenset({(node_id,a.alias)})))
            if not self.relation_aggregated[n.left_id] or not self.relation_aggregated[n.right_id]: fail('unsafe_raw_set_operation')
            operator={'union':exp.Union,'intersect':exp.Intersect,'except':exp.Except}[n.operator]
            tree=operator(this=exp.select('*').from_(exp.to_table(n.left_id,quoted=True)),expression=exp.select('*').from_(exp.to_table(n.right_id,quoted=True)),distinct=not n.all)
        else:
            inputs = self.child(n.input_id)
            slots = list(inputs)
            tree = exp.select(*[exp.column(s.alias,quoted=True) for s in inputs]).from_(exp.to_table(n.input_id,quoted=True))
            if n.kind == 'filter':
                predicate, info = self.expression(n.predicate,inputs)
                if info.data_type != 'boolean': fail('non_boolean_filter')
                tree=tree.where(predicate)
            elif n.kind == 'project':
                trees,slots=self.named(n.outputs,inputs,node_id,'scalar')
                tree.set('expressions',trees)
            elif n.kind == 'aggregate':
                groups, gs=self.named(n.group_by,inputs,node_id,'group') if n.group_by else ([],[])
                measures, ms=self.named(n.measures,inputs,node_id,'measure')
                if any(not x.aggregated for x in ms): fail('invalid_aggregate_placement')
                if any(x.classification in ('restricted','confidential') for x in gs): fail('sensitive_group_key')
                slots=gs+ms
                if len({x.alias for x in slots})!=len(slots): fail('duplicate_output_alias')
                tree.set('expressions',groups+measures)
                if groups: tree=tree.group_by(*[exp.Literal.number(i+1) for i in range(len(groups))])
                sensitive = [x for x in ms if x.sources and x.classification in ('restricted','confidential') and x.reducing]
                if sensitive:
                    # Count is safe without a contributor threshold; other sensitive
                    # numeric reductions require one count per contributing column.
                    needs = [x for item,x in zip(n.measures,ms) if x in sensitive and not (item.expression.kind=='function' and item.expression.function=='count')]
                    if needs:
                        minimum=n.minimum_group_size
                        if minimum is None: fail('aggregate_group_size_required')
                        self.aggregate_guards.append(('minimum',minimum))
                        bound,info=self.parameter(minimum)
                        if info.data_type!='integer': fail('literal_type_mismatch')
                        for source in sorted({s for x in needs for s in x.sources},key=lambda x:(x.table_id,x.column)):
                            slot=self.physical(source,inputs)
                            tree=tree.having(exp.GTE(this=exp.Count(this=exp.column(slot.alias,quoted=True)),expression=bound.copy()))
            elif n.kind == 'sort':
                trees=[]
                for key in n.keys:
                    expression,_=self.expression(key.expression,inputs)
                    trees.append(exp.Ordered(this=expression,desc=key.direction=='desc',nulls_first=key.nulls=='first'))
                tree=tree.order_by(*trees)
            elif n.kind == 'limit':
                bound,info=self.parameter(n.count)
                if info.data_type!='integer': fail('literal_type_mismatch')
                self.aggregate_guards.append(('positive',n.count)); self.limits.append((node_id,n.count))
                tree=tree.limit(bound)
            elif n.kind == 'window':
                if self.route!='planned_ir': fail('unsupported_complexity')
                for win in n.outputs:
                    args=[]; infos=[]
                    if win.function in ('lag','lead'):
                        if win.argument is None: fail('invalid_function_signature')
                        argument,info=self.expression(win.argument,inputs)
                        args.append(argument); infos.append(info)
                        if win.offset:
                            offset,oi=self.parameter(win.offset)
                            if oi.data_type!='integer': fail('literal_type_mismatch')
                            self.aggregate_guards.append(('positive',win.offset)); args.append(offset)
                    elif win.argument is not None or win.offset is not None: fail('invalid_function_signature')
                    if not win.order_by: fail('invalid_window_placement')
                    orders=[]
                    for key in win.order_by:
                        value,order_info=self.expression(key.expression,inputs)
                        infos.append(order_info)
                        orders.append(exp.Ordered(this=value,desc=key.direction=='desc',nulls_first=key.nulls=='first'))
                    partition_pairs=[self.expression(x,inputs) for x in win.partition_by]
                    partitions=[x[0] for x in partition_pairs]
                    infos.extend(x[1] for x in partition_pairs)
                    value=exp.Window(this=exp.func(win.function.upper(),*args),partition_by=partitions,order=exp.Order(expressions=orders))
                    tree.append('expressions',exp.alias_(value,win.alias,quoted=True))
                    info=merged(infos,infos[0].data_type if win.function in ('lag','lead') else 'integer',reducing=all(x.reducing for x in infos),aggregated=all(x.aggregated for x in infos))
                    slots.append(replace(info,alias=win.alias,origins=frozenset({(node_id,win.alias)})))
                if len({x.alias for x in slots})!=len(slots): fail('duplicate_output_alias')
            else: fail('unsupported_node')
        if n.kind == 'scan': aggregated_relation = False
        elif n.kind == 'aggregate': aggregated_relation = True
        elif n.kind in ('join', 'set_operation'): aggregated_relation = self.relation_aggregated[n.left_id] and self.relation_aggregated[n.right_id]
        else: aggregated_relation = self.relation_aggregated[n.input_id]
        self.relation_aggregated[node_id] = aggregated_relation
        slots=[replace(x,origins=x.origins|{(node_id,x.alias)}) for x in slots]
        self.ctes.append((node_id,tree)); self.built[node_id]=slots; self.visiting.remove(node_id)

    def check_assumptions(self):
        def walk(value):
            if isinstance(value,m.BaseModel):
                yield value
                for field in type(value).model_fields:
                    yield from walk(getattr(value,field))
            elif isinstance(value,tuple):
                for item in value: yield from walk(item)
        terms=list(walk(self.ir))
        used_columns={x.ref for x in terms if isinstance(x,m.ColumnExpression)}
        def matches(predicate,column,refs):
            if isinstance(predicate,m.BinaryExpression):
                if predicate.operator=='and': return matches(predicate.left,column,refs) or matches(predicate.right,column,refs)
                if predicate.operator=='eq':
                    for a,b in ((predicate.left,predicate.right),(predicate.right,predicate.left)):
                        if isinstance(a,m.ColumnExpression) and a.ref==column and isinstance(b,m.LiteralExpression) and len(refs)==1 and b.ref==refs[0]: return True
            if isinstance(predicate,m.InExpression):
                return not predicate.negated and isinstance(predicate.expression,m.ColumnExpression) and predicate.expression.ref==column and {digest(x.ref) for x in predicate.values}=={digest(x) for x in refs}
            return False
        for assumption in self.ir.assumptions:
            if assumption.kind=='status':
                if not any(isinstance(x,m.FilterNode) and matches(x.predicate,assumption.column,assumption.operands) for x in terms): fail('invalid_assumption')
            elif assumption.kind=='snapshot':
                if assumption.column not in used_columns: fail('invalid_assumption')
            elif assumption.kind=='grain':
                if not any(isinstance(x,m.AggregateNode) and any(isinstance(g.expression,m.ColumnExpression) and g.expression.ref==assumption.column for g in x.group_by) for x in terms): fail('invalid_assumption')
            else:
                if not any(isinstance(x,m.CaseExpression) and any(matches(b.when,assumption.column,assumption.operands) for b in x.branches) and any(matches(b.when,assumption.column,assumption.secondary_operands) for b in x.branches) for x in terms): fail('invalid_assumption')
            if self.resolve and assumption.operands:
                first=[resolve_literal(x,self.question,self.snapshot) for x in assumption.operands]
                second=[resolve_literal(x,self.question,self.snapshot) for x in assumption.secondary_operands]
                if any(v is None or v=='' for v in first+second) or len(set(first+second))!=len(first+second): fail('invalid_assumption')
        for ref in used_columns:
            if ref.column=='satisfaction_score':
                if not any(a.kind=='status' and a.column.table_id==ref.table_id and a.column.column=='status' for a in self.ir.assumptions): fail('missing_status_assumption')
            if ref.table_id=='table.accounts' and ref.column=='balance':
                if not any(a.kind=='snapshot' and a.column==ref for a in self.ir.assumptions): fail('missing_snapshot_assumption')

    def finish(self):
        self.build(self.ir.root_node_id)
        if len(self.built)!=len(self.nodes) or not any(n.kind=='scan' for n in self.nodes.values()): fail('invalid_ir_shape')
        # Multi-fact fanout and raw event unions are forbidden, even when all edges exist.
        for pair in ({'table.transactions','table.card_transactions'}, {'table.loans','table.employees'}):
            if pair<=self.used:
                for node in self.nodes.values():
                    if node.kind=='join':
                        l,r=self.built[node.left_id],self.built[node.right_id]
                        lt={s.table_id for x in l for s in x.sources}; rt={s.table_id for x in r for s in x.sources}
                        if (pair & lt) and (pair & rt) and (pair & lt)!=(pair & rt):
                            if not all(x.aggregated or x.physical for x in l+r) or not any(x.aggregated for x in l) or not any(x.aggregated for x in r): fail('unsafe_fact_fanout')
        self.used|={o.object_id for o in self.snapshot.objects if o.object_type in ('concept','policy') and set(o.dependencies)&self.used}
        declared={(w.object_id,w.warning_hash,w.control_id) for w in self.ir.warning_decisions}
        for decision in self.ir.warning_decisions:
            obj=self.objects.get(decision.object_id)
            if not obj or not any((w.warning_hash,w.control_id)==(decision.warning_hash,decision.control_id) for w in obj.warnings): fail('unknown_warning')
        for object_id in self.used:
            for w in self.objects[object_id].warnings:
                if w.control_id=='unsupported_warning': fail('unsupported_warning',object_id)
                if (object_id,w.warning_hash,w.control_id) not in declared: fail('unaddressed_warning',object_id)
        for assumption in self.ir.assumptions:
            if assumption.column not in self.columns or (not assumption.operands and assumption.kind in ('status','direction')): fail('invalid_assumption')
            if assumption.kind=='direction' and not assumption.secondary_operands: fail('invalid_assumption')
            refs=assumption.operands+assumption.secondary_operands
            for ref in refs: self.parameter(ref)
        self.check_assumptions()
        final=self.built[self.ir.root_node_id]
        sources={s for x in final for s in x.sources}
        requested={s for d in self.ir.requested_disclosures for s in d.sources}
        if not requested<=sources: fail('invalid_disclosure')
        sensitive=[x for x in final if x.classification in ('restricted','confidential') and not x.reducing]
        # Only a limit on the final unary chain bounds final disclosure. A limit
        # hidden in a CTE before a join/aggregate is not accepted as an output cap.
        chain=set(); node=self.nodes[self.ir.root_node_id]
        while node.kind in ('limit','sort','project','window'):
            chain.add(node.node_id); node=self.nodes[node.input_id]
        effective=self.max_rows
        if self.resolve:
            for kind,ref in self.aggregate_guards:
                val=resolve_literal(ref,self.question,self.snapshot,'integer')
                if val<(5 if kind=='minimum' else 1): fail('invalid_literal_bound')
            for node_id,ref in self.limits:
                if node_id in chain: effective=min(effective,resolve_literal(ref,self.question,self.snapshot,'integer'))
        if sensitive:
            if 'policy.sensitive-banking-data' not in self.objects: fail('policy_disallowed')
            if not {s for x in sensitive for s in x.sources}<=requested: fail('policy_disallowed')
            if self.resolve and effective>50: fail('policy_disallowed')
        tree=exp.select(*[exp.column(x.alias,quoted=True) for x in final]).from_(exp.to_table(self.ir.root_node_id,quoted=True)).limit(self.max_rows+1)
        for node_id,body in self.ctes: tree=tree.with_(exp.to_identifier(node_id,quoted=True),as_=body)
        parameters=[]
        positions={}
        for placeholder in tree.find_all(exp.Placeholder):
            original=placeholder.this
            if original not in positions:
                ref,value,typ=self.pending[int(original[1:])]
                if ref is not None and self.resolve: value=resolve_literal(ref,self.question,self.snapshot)
                positions[original]=len(parameters)+1
                parameters.append(m.BoundParameter(position=len(parameters)+1,data_type=typ,value=value))
            placeholder.set('this',str(positions[original]))
        sql=tree.sql(dialect='duckdb')
        query=m.CompiledQuery(sql=sql,parameters=tuple(parameters),ir_hash=digest(self.ir),compiler_version=COMPILER_VERSION,dialect='duckdb')
        lineage=tuple(m.OutputLineage(output_name=x.alias,data_type=x.data_type,sources=x.sources,classification=x.classification,reducing=x.reducing,metric_ids=x.metrics) for x in final)
        disclosures=tuple(m.DisclosureRecord(output_name=x.alias,sources=x.sources,row_bound=effective) for x in sensitive) if self.resolve else ()
        usage=m.GroundingUsage(object_ids=tuple(sorted(self.used)),warnings=tuple(m.WarningRef(object_id=w.object_id,warning_hash=w.warning_hash) for o in self.snapshot.objects if o.object_id in self.used for w in o.warnings),metrics=tuple(self.objects[x] for x in sorted(self.metric_ids)))
        return Compilation(query,lineage,disclosures,usage)


def validate_ir(ir, snapshot, question, generation_route='default_ir', accepted_complex_plan_hash=None, max_rows=1000):
    verify_snapshot(snapshot)
    ir=m.RelationalQueryIR.model_validate(ir.model_dump())
    if generation_route not in ('default_ir','planned_ir') or (generation_route=='planned_ir') != bool(accepted_complex_plan_hash): fail('invalid_generation_route')
    _Builder(ir,snapshot,question,generation_route,max_rows,resolve=False).finish()
    return m.ValidatedIR(ir=ir,ir_hash=digest(ir),snapshot_hash=snapshot.snapshot_hash,canonical_question_hash=canonical_question_sha256(question),generation_route=generation_route,accepted_complex_plan_hash=accepted_complex_plan_hash)


class DialectCompiler:
    def compile(self, validated_ir, snapshot, canonical_question, max_rows=1000):
        verify_snapshot(snapshot)
        if validated_ir.ir_hash!=digest(validated_ir.ir) or validated_ir.snapshot_hash!=snapshot.snapshot_hash or validated_ir.canonical_question_hash!=canonical_question_sha256(canonical_question): fail('validated_ir_integrity_error')
        # Shape/type validation always precedes any value resolution, also on cache hits.
        validate_ir(validated_ir.ir,snapshot,canonical_question,validated_ir.generation_route,validated_ir.accepted_complex_plan_hash,max_rows)
        return _Builder(validated_ir.ir,snapshot,canonical_question,validated_ir.generation_route,max_rows,resolve=True).finish()


def authorize_compiled(compilation, validated_ir, snapshot, question, max_rows=1000):
    expected=_Builder(validated_ir.ir,snapshot,question,validated_ir.generation_route,max_rows,resolve=True).finish()
    try:
        actual_tree=sqlglot.parse_one(compilation.query.sql,read='duckdb')
        expected_tree=sqlglot.parse_one(expected.query.sql,read='duckdb')
    except Exception:
        fail('compiled_sql_containment_error')
    if actual_tree!=expected_tree or compilation.lineage!=expected.lineage or compilation.disclosures!=expected.disclosures or compilation.query.parameters!=expected.query.parameters:
        fail('compiled_sql_containment_error')
    return expected
