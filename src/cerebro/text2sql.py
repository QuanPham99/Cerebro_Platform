"""One-call typed IR orchestration; no model-written SQL or semantic repair."""
from __future__ import annotations
import hashlib
from pydantic import ValidationError
from . import query_models as m
from .complexity import ComplexityRouter
from .literals import spans
from .prompting import GuardedProvider,validate_ingress
from .provenance import QueryFault,digest
from .query_budget import RequestBudget
from .sql_compiler import DialectCompiler,validate_ir,authorize_compiled
from .text2sql_provider import GuardedGenerationRequest
from .text2sql_cache import cache_key

LITERAL_ERRORS={'invalid_question_span','unparseable_question_literal','ungrounded_governed_literal','literal_type_mismatch'}


def validate_clarification(proposal,question,snapshot):
    objects={x.object_id for x in snapshot.objects}
    relationships={r.relationship_id for o in snapshot.objects for r in o.relationships}
    literals={x.literal_id for x in snapshot.governed_literals}
    for ambiguity in proposal.ambiguities:
        if (ambiguity.start,ambiguity.end) not in spans(question): raise QueryFault('invalid_clarification_request')
        candidates=ambiguity.candidates
        if len({digest(x) for x in candidates})!=len(candidates) or len({x.kind for x in candidates})!=1: raise QueryFault('invalid_clarification_request')
        word=question[ambiguity.start:ambiguity.end].lower()
        for c in candidates:
            if c.kind=='object' and c.object_id not in objects: raise QueryFault('invalid_clarification_request')
            if c.kind=='relationship' and c.relationship_id not in relationships: raise QueryFault('invalid_clarification_request')
            if c.kind=='governed_literal' and c.literal_id not in literals: raise QueryFault('invalid_clarification_request')
            if c.kind=='grain':
                if word not in ('period','recent','time') or any(ref not in {v.ref for o in snapshot.objects for v in o.columns} for ref in c.grouping_columns): raise QueryFault('invalid_clarification_request')
            if c.kind=='operator' and word not in ('compare','combine'): raise QueryFault('invalid_clarification_request')
    return proposal.ambiguities


class Text2SQLAgent:
    def __init__(self,provider,resolver,executor,*,cache=None,budget_limits=None):
        if type(provider) is not GuardedProvider or resolver is None or executor is None or not callable(getattr(executor,'explain',None)):
            raise ValueError('mandatory_query_dependencies_missing')
        self.provider,self.resolver,self.executor=provider,resolver,executor
        self.cache,self.budget_limits=cache,budget_limits
        self.compiler=DialectCompiler();self.router=ComplexityRouter()

    def run(self,request: m.SQLGenerationRequest):
        if type(request) is not m.SQLGenerationRequest: raise TypeError('strict_query_request_required')
        budget=RequestBudget(self.budget_limits)
        question=m.canonicalize_question(request.question)
        qhash=hashlib.sha256(question.encode()).hexdigest()
        snapshot=None;ir=None;route=None;generation_route='none';cache_status='disabled' if self.cache is None else 'miss'
        compilation=None;violations=[];usage=m.GroundingUsage()
        def common():
            return dict(contract_version='008.v3',semantic_version=self.resolver.bundle.version,
                policy_version=request.authorization_scope.policy_version,canonicalization_version='008.question.v1',
                literal_registry_version='008.literal-span.v1',ir_contract_version='008.ir.v1',type_registry_version='008.types.v1',
                prompt_version='008.prompt.v1',router_version=self.router.version,compiler_version='008.compiler.v1',checker_version='008.checker.v1',
                dialect=request.dialect,provider=self.provider.name,model=self.provider.model,model_revision=self.provider.model_revision,
                canonical_question_hash=qhash,authorization_scope_hash=request.authorization_scope.authorization_scope_hash,
                snapshot_hash=snapshot.snapshot_hash if snapshot else None,generation_route=generation_route,cache_status=cache_status,
                grounding_usage=usage,assumptions=ir.assumptions if ir else (),attempt_records=tuple(budget.records),budget_usage=budget.usage(),violations=tuple(violations))
        def refuse(reason,**kwargs): return m.RefusedResponse(**common(),reason=reason,**kwargs)
        def call(mode):
            budget.before_call(mode)
            # Conservative pre-contact input bound includes the schema and metadata.
            estimate=(len(snapshot.model_dump_json())+len(question)+len(str(m.OUTCOME_ADAPTER.json_schema()))+2)//3
            if budget.input+estimate>budget.limits.max_input_tokens: raise QueryFault('budget_exceeded')
            result=self.provider.generate(GuardedGenerationRequest(question,snapshot,mode,route),m.IR_ADAPTER if mode=='planned_ir' else m.OUTCOME_ADAPTER,budget=budget)
            budget.account(result)
            for code in result.recovered_codes: violations.append(m.CheckViolation(code=code))
            budget.record('provider',codes=result.recovered_codes)
            return result.output
        try:
            budget.check();validate_ingress(question)
            snapshot=self.resolver.resolve(question,request.authorization_scope,request.dialect)
            budget.record('grounding')
            if not any(x.object_type=='table' for x in snapshot.objects):
                return refuse('missing_grounding')
            if self.cache:
                for name in ('default_ir','planned_ir'):
                    found=self.cache.get(cache_key(snapshot,qhash,self.provider,name,request.max_rows),snapshot)
                    if found:
                        cached,route=found;ir=cached.ir;generation_route=cached.generation_route;cache_status='hit';break
                budget.record('cache',cache_status)
            if ir is None:
                outcome=call('default_ir');generation_route='default_ir'
                if isinstance(outcome,m.GroundingRefusal):
                    if not outcome.unmet_needs or any(n.object_id in {o.object_id for o in snapshot.objects} for n in outcome.unmet_needs): raise QueryFault('false_grounding_refusal')
                    return refuse('missing_grounding',unmet_needs=outcome.unmet_needs)
                if isinstance(outcome,m.ClarificationRequest):
                    ambiguities=validate_clarification(outcome,question,snapshot);budget.record('clarification')
                    return refuse('clarification_required',ambiguities=ambiguities)
                if isinstance(outcome,m.ComplexQueryPlan):
                    route=self.router.accept(outcome,snapshot);budget.record('router')
                    budget.authorize_planned_ir(route,snapshot)
                    ir=call('planned_ir');generation_route='planned_ir'
                else: ir=outcome
            if route: self.router.check_fallback(ir,route,snapshot)
            budget.check()
            valid=validate_ir(ir,snapshot,question,generation_route,route.plan_hash if route else None,request.max_rows)
            budget.record('ir_validation')
            compilation=self.compiler.compile(valid,snapshot,question,request.max_rows)
            budget.record('compilation')
            compilation=authorize_compiled(compilation,valid,snapshot,question,request.max_rows)
            usage=compilation.usage;budget.record('ast_authorization');budget.check()
            self.executor.explain(compilation.query,remaining_ms=budget.remaining_ms)
            budget.record('explain');budget.check()
            result=self.executor(compilation.query,request.max_rows,remaining_ms=budget.remaining_ms)
            budget.record('execution');budget.check()
            if self.cache and cache_status!='hit': self.cache.put(cache_key(snapshot,qhash,self.provider,generation_route,request.max_rows),ir,generation_route,route)
            artifact=m.SQLArtifact(sql=compilation.query.sql,sql_sha256=hashlib.sha256(compilation.query.sql.encode()).hexdigest(),parameter_count=len(compilation.query.parameters),parameter_types=tuple(p.data_type for p in compilation.query.parameters),ir_hash=valid.ir_hash,compiler_version=compilation.query.compiler_version,dialect=request.dialect)
            return m.OkResponse(**common(),ir=ir,sql_artifact=artifact,result=result,output_lineage=compilation.lineage,disclosures=compilation.disclosures)
        except QueryFault as fault:
            budget.transports+=fault.transport_attempts
            subjects={x.object_id for x in snapshot.objects} if snapshot else set()
            violations.append(m.CheckViolation(code=fault.code,subject=fault.subject if fault.subject in subjects else ''))
            budget.record('terminal','failed',(fault.code,))
            if fault.code=='policy_disallowed': return refuse('policy_disallowed',policy_ids=('policy.sensitive-banking-data',))
            if fault.code=='unsupported_complexity': return refuse('unsupported_complexity')
            if fault.code in LITERAL_ERRORS:
                return refuse('clarification_required',literal_needs=(m.LiteralClarificationNeed(issue=fault.code,expected_type='string'),))
            return m.CheckFailedResponse(**common(),ir=ir)
        except (ValidationError,ValueError,TypeError):
            violations.append(m.CheckViolation(code='invalid_runtime_contract'))
            budget.record('terminal','failed',('invalid_runtime_contract',))
            return m.CheckFailedResponse(**common(),ir=ir if isinstance(ir,m.RelationalQueryIR) else None)
