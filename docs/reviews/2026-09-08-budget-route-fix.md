# Request-budget route sequencing repair

Scope: continuation of the existing Option B Text-to-SQL work, limited to FR-716 / T-711. This report does not certify the complete Stage 2 implementation or supersede the September 7 review.

The first call accepts only default_ir. After locally authorized complex routing, the second accepts only planned_ir. Invalid modes fail with budget_exceeded without consuming a call. Existing working-tree changes were preserved.

## Traceability

- Specification/test design: specs/008-text-to-sql-agent.md, FR-716 / T-711.
- Implementation: src/cerebro/query_budget.py, RequestBudget.before_call.
- Regression: tests/test_query_budget_cache.py, test_budget_rejects_unknown_mode_without_consuming_call (four cases) and test_budget_second_call_requires_planned_mode.
- Before repair: targeted suite, 5 failed and 5 passed; all five new cases failed because QueryFault was not raised.
- After repair: full offline suite, 122 passed in 18.35s.

## Commands and evidence

- `CEREBRO_TEST_NO_NETWORK=1 env -u CEREBRO_API_KEY -u OPENAI_API_KEY -u CEREBRO_MODEL .venv/bin/python -m pytest tests/test_query_budget_cache.py -q`: reproduced five failures before implementation.
- `CEREBRO_TEST_NO_NETWORK=1 env -u CEREBRO_API_KEY -u OPENAI_API_KEY -u CEREBRO_MODEL .venv/bin/python -m pytest -q`: PASS after implementation.
- `.venv/bin/python -m compileall -q src scripts tests`: PASS.
- `git diff --check`: PASS.
- Live provider and authoritative-data verification: not run; outside this local repair.
- Dedicated lint/type/build checks: not run; compilation and diff checks cover this narrow change, but do not constitute those checks.

## Compliance matrix

| Controls | Status | Evidence |
|---|---|---|
| C-01, C-02, C-03 | PASS | FR-716 and T-711 clarified before production edit |
| C-04, C-05 | PASS | Implementation and regression mapping above |
| C-06 | PASS | 122 offline tests passed |
| C-07 | PARTIAL | Compilation/diff checks pass; dedicated lint/type/build not run |
| C-08, C-09 | PASS | Existing fault contract; invalid call modes rejected, valid planned route retained |
| C-10 | PASS | Actual failing and passing commands recorded |
| C-11, C-12 | PASS | Three scoped source/spec/test edits plus this report |

Status: PARTIALLY COMPLIANT. No exceptions claimed. Remaining verification: dedicated static/build checks if required for release. Full Stage 2 and live baseline remain uncertified; this result covers only the budget-route repair.
