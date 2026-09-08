"""Spec 008 deterministic gates. Raw model-written SQL is no longer an input."""
from .sql_compiler import validate_ir, authorize_compiled
from .grounding import verify_snapshot
from .text2sql import validate_clarification

__all__ = ["validate_ir", "authorize_compiled", "verify_snapshot", "validate_clarification"]
