from decimal import Decimal
import time
from .query_models import BudgetLimits,BudgetUsage,AttemptRecord
from .complexity import verify_route
from .provenance import QueryFault

class RequestBudget:
    def __init__(self,limits=None,clock=time.monotonic):
        self.limits=limits or BudgetLimits()
        self.clock=clock; self.started=clock(); self._capacity=1; self.calls=0
        self.transports=0; self.input=0; self.output=0; self.cost=Decimal('0'); self.records=[]

    @property
    def capacity(self): return self._capacity

    def check(self):
        if self.remaining_ms<=0 or self.input>self.limits.max_input_tokens or self.output>self.limits.max_output_tokens or self.cost>self.limits.max_cost_usd:
            raise QueryFault('budget_exceeded')

    @property
    def remaining_ms(self):
        return self.limits.end_to_end_deadline_ms-int((self.clock()-self.started)*1000)

    def authorize_planned_ir(self,decision,snapshot):
        verify_route(decision,snapshot)
        if self.capacity!=1 or self.calls!=1: raise QueryFault('budget_exceeded')
        self.check(); self._capacity=2

    def before_call(self,mode):
        self.check()
        expected_mode = 'default_ir' if self.calls == 0 else 'planned_ir'
        if self.calls >= self.capacity or mode != expected_mode:
            raise QueryFault('budget_exceeded')
        self.calls+=1

    def account(self,generation):
        self.transports+=generation.transport_attempts
        self.input+=generation.input_tokens; self.output+=generation.output_tokens
        self.cost+=Decimal(generation.cost_usd)
        if not 1<=generation.transport_attempts<=self.limits.max_transport_attempts_per_semantic_call: raise QueryFault('budget_exceeded')
        self.check()

    def record(self,domain,outcome='ok',codes=(),elapsed_ms=0):
        self.records.append(AttemptRecord(domain=domain,phase=domain,ordinal=len(self.records)+1,outcome=outcome,codes=tuple(codes),elapsed_ms=elapsed_ms))

    def usage(self):
        return BudgetUsage(semantic_call_capacity=self.capacity,planned_ir_authorized=self.capacity==2,semantic_calls=self.calls,transport_attempts=self.transports,input_tokens=self.input,output_tokens=self.output,cost_usd=self.cost,elapsed_ms=int((self.clock()-self.started)*1000))
