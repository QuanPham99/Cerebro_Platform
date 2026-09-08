# Review Stage 2 — 2026-09-07

## Kết luận và phạm vi

Stage 2 chưa đáp ứng implementation baseline hiện tại. Spec 008 và implementation plan đã chuyển sang Option B (typed IR → deterministic compiler), trong khi code và phần lớn tests vẫn là prototype hai bước QueryPlan → model-written SQL. Có lỗi an toàn và lỗi bảo toàn dữ liệu đã tái hiện được ngay trong prototype, không chỉ là thiếu các tính năng mới.

Phạm vi: Phase 2 theo `specs/README.md`, gồm specs 008–010; plan `docs/superpowers/plans/2026-08-27-text-to-sql-agent.md`; toàn bộ module Text-to-SQL, checker, provider, executor, loader, evaluation, contract và CLI liên quan; tests và golden answers. Review bao gồm thay đổi tracked/untracked đang có trong working tree. Không sửa production code, không thay đổi các chỉnh sửa có sẵn của người dùng, không gọi hosted provider và không load lại dữ liệu thật.

P1 = cần giải quyết trước khi dùng đường thực thi với dữ liệu thật hoặc công nhận đạt Stage 2. P2 = cần cập nhật để contract, kiểm thử và khả năng tái lập đáng tin cậy. Các phát hiện spec-only được ghi rõ, không được coi là lỗi runtime đã tái hiện.

## Phát hiện theo mức ưu tiên

### R01 — P1: SQL thực tế không bị ràng buộc bởi grounding/plan

Vị trí: `src/cerebro/selfcheck.py:129`, `src/cerebro/text2sql.py:176`, `src/cerebro/executor.py:36`.

`check_plan` chỉ kiểm tra các ID model khai báo. `check_sql` không kiểm tra bảng/cột/join/filter/function thực sự được SQL sử dụng. Một plan hợp lệ chỉ khai báo `table.customers` có thể đi kèm `SELECT secret FROM private_table`; với bảng giả có trong database, agent đã trả `ok` và giá trị từ bảng ngoài grounding. Một join trực tiếp `transactions.account_id = branches.branch_id` cũng không bị checker bắt.

`SELECT * FROM read_csv(...)` vượt checker. Probe riêng trên cùng loại kết nối `read_only=True` đọc được CSV giả bên ngoài database. Read-only không tạo ra giới hạn chỉ được đọc các bảng trong bundle.

Cần: authorization-first snapshot, IR containment, compiler và AST/function/source allowlist; mọi nguồn ngoài snapshot phải bị chặn trước `EXPLAIN`. Regression: plan/SQL khác bảng, cột cùng tên khác bảng, sai join, CTE/subquery và external scan. Trace: FR-704, FR-709/709a, FR-712; AC-700/705/709.

### R02 — P1: Disclosure check bỏ lọt wildcard và aggregate giữ nguyên giá trị

Vị trí: `src/cerebro/selfcheck.py:148`.

Checker bỏ qua toàn bộ projection chỉ cần chứa một `AggFunc`; `*` không được expand thành các cột có classification. Đã xác nhận cả `SELECT * FROM customers`, `SELECT list(name) FROM customers`, `SELECT MIN(name) FROM customers` đều trả danh sách violations rỗng dù không có disclosure bound. `list(name)` có thể đóng gói nhiều giá trị nhạy cảm trong một row nên result-row cap không khắc phục được.

Cần: lineage theo qualified source column; expand wildcard hoặc cấm nó; chặn collection/string aggregates; MIN/MAX và các phép giữ nguyên giá trị phải theo disclosure path; numeric aggregate phải có minimum contributor control. Regression phải bao gồm aggregate lồng trong biểu thức, alias, CASE, cast và nhiều nguồn sensitive. Trace: FR-713; AC-701.

### R03 — P1: Load thất bại vẫn phá dữ liệu database hiện có

Vị trí: `scripts/load_duckdb.py:65`.

Loader mở database đích trước khi preflight tất cả CSV, rồi DROP/CREATE/INSERT từng bảng với các thay đổi đã commit. Probe trên database tạm: ban đầu `accounts.account_id = 999`; thiếu file `transactions.csv` ở cuối làm `LoadError`, nhưng accounts đã đổi thành `1` và SHA-256 database đã thay đổi.

Tests hiện chỉ assert có exception, không assert dữ liệu đích được bảo toàn. Chưa kiểm tra extra CSV, manifest/checksum, số bảng chính xác hay phát hành materialization receipt.

Cần: preflight toàn bộ file/header trước khi mở target; load và verify database tạm rồi atomically replace; late cast failure cũng phải giữ target byte-for-byte. Thêm regression missing-late-file, late-invalid-header, late-cast-failure, extra-file và receipt mismatch. Trace: FR-700/701; AC-712.

### R04 — P1: Raw execution error được đưa trở lại provider; recovered fault bị xóa

Vị trí: `src/cerebro/text2sql.py:95`, `src/cerebro/text2sql.py:187`, `src/cerebro/hosted_provider.py:129`, `src/cerebro/evaluation.py:37`.

`str(exc)` trở thành violation.message, sau đó `_render` đưa message vào prompt SQL retry. Probe với lỗi chứa sentinel giả `SYNTHETIC_PRIVATE_ENGINE_VALUE`, qua cả `EgressGuard`, xác nhận sentinel có mặt trong prompt thứ ba. Lần retry thành công trả `ok` với `violations=[]`, mất dấu lỗi đã phục hồi.

Guard chỉ dùng substring từ tối đa 50 giá trị distinct mỗi cột sensitive, bỏ qua numeric và chuỗi ngắn. Nó không bao phủ raw exception hoặc giá trị ngoài sample; việc đọc sample cũng trái với egress-by-construction của contract mới. Cassette còn lưu trực tiếp serialized model output, bao gồm SQL/plan có thể mang literal.

Cần: typed guarded request/envelope với exact membership; sanitized error codes/subjects; không gọi model lại sau lỗi compiler/engine/execution; giữ attempt history kể cả terminal success. Không mở rộng việc lấy mẫu để vá guard. Trace: FR-703a/b, FR-708, FR-716/716a, FR-721; AC-707/711/714.

### R05 — P1: `ok` không bảo đảm đã EXPLAIN/execute; response cho phép trạng thái mâu thuẫn

Vị trí: `src/cerebro/text2sql.py:178`, `src/cerebro/text2sql.py:207`, `src/cerebro/models.py:209`.

`connection` và `execute` đều optional. Đã tái hiện agent trả `ok` với `result=None` khi không có cả hai. Cũng có thể inject executor nhưng không có validator. Model response chấp nhận `status='refused'` cùng SQL và result rows; các model mới không dùng discriminated strict union.

`SQLGenerationRequest` vẫn nhận `GroundingResponse` từ caller, không có trusted `AuthorizationScope`/resolver, canonical question hay immutable snapshot. Không có accepted IR, parameterized SQL artifact, lineage/disclosure, route/cache evidence và budgets.

Cần thực hiện migration contract/composition root theo plan Tasks 2–3 và 10; bắt buộc validator + executor; reject illegal response combinations và caller-supplied grounding. Đây là thay đổi kiến trúc, không thể coi hoàn tất bằng vài kiểm tra bổ sung vào QueryPlan cũ. Trace: FR-704–708, FR-714/718; AC-713.

### R06 — P1: Có thể thỏa metric check bằng công thức trong CTE không được dùng

Vị trí: `src/cerebro/selfcheck.py:97`, `src/cerebro/selfcheck.py:132`.

`_projections` gom projection từ mọi SELECT, không chứng minh projection đó dẫn đến output được trả. Probe sau vượt check dù kết quả chỉ là hằng số sai:

```sql
WITH unused AS (
  SELECT 100.0 * SUM(card_transactions.is_fraud) / NULLIF(COUNT(*), 0) AS r
  FROM card_transactions
)
SELECT 999 AS fraud_rate
```

Cần compile từ `MetricExpression(metric_id)` và kiểm tra root-equivalence trên output lineage; không chỉ tìm công thức xuất hiện đâu đó trong AST. Regression: dead CTE, wrong-source same-name column, wrapper và changed denominator. Trace: FR-711; AC-702.

### R07 — P1: Warning/time semantics hiện chỉ là prompt và lời ghi chú

Vị trí: `src/cerebro/selfcheck.py:72`, `src/cerebro/text2sql.py:26`, `tests/golden_answers.py:125`.

Copy warning text vào `warnings_addressed` đủ để vượt plan check mà không kiểm tra control trong SQL. `CURRENT_DATE` vẫn vượt SQL checker. Mapping inflow/outflow được hard-code trong lời ghi chú; không chứng minh SQL đã dùng mapping đó hay người dùng đã cung cấp các toán hạng cần thiết.

GQ-08 hỏi monthly transaction growth nhưng golden SQL chỉ trả monthly SUM; không có previous-period value, delta hoặc growth rate. Test baseline chỉ tìm `MAX(` và vắng `CURRENT_DATE`, nên đánh dấu SQL sai ý nghĩa là đạt.

Cần warning control registry, typed assumptions/literal refs, kiểm tra anchor và semantics. GQ-08 cần fixture ít nhất ba tháng với số liệu dễ kiểm tra bằng tay, có tháng thiếu/zero denominator để xác nhận growth. Trace: FR-710/715; AC-703.

### R08 — P1: Không có deadline/budget xuyên suốt; provider faults thoát khỏi contract

Vị trí: `src/cerebro/selfcheck.py:172`, `src/cerebro/executor.py:39`, `src/cerebro/hosted_provider.py:49`, `src/cerebro/text2sql.py:133`.

EXPLAIN không có watchdog/deadline. Execute dùng Timer nhưng chỉ `cancel()`, không join callback trước connection reuse; đây là race risk qua code inspection, chưa tái hiện race trong review. Không có request-wide token/cost/deadline budget hay cấu hình resource limits như spec mới. Hiện tối đa hai plan attempts cộng hai SQL attempts; hosted transport mặc định bốn attempts và timeout 120s/attempt.

`ProviderUnavailable` không thuộc các exception agent bắt. Probe xác nhận exception thoát khỏi `agent.run`; CLI chỉ chuyển thành generic error, không có typed terminal response. Timeout bị gộp vào `execution_error` và còn kích hoạt SQL retry.

Cần RequestBudget, typed error domains, mandatory bounded EXPLAIN, synchronized watchdog cleanup; request row/time bounds phải được validate. Trace: FR-703b, FR-714, FR-716/716a, FR-719/721.

### R09 — P1: Baseline chưa đủ điều kiện làm bằng chứng live hoặc đầu vào adaptation

Vị trí: `src/cerebro/evaluation.py:90`, `tests/test_baseline_evaluation.py:22`, `src/cerebro/cli.py:99`.

`run_sql_baseline` nhận cả GoldenProvider nhưng không phân biệt `offline_reference` với `live_unadapted_baseline`. Artifact lưu nguyên question và SQL literal; thiếu materialization/capability receipts, scope/snapshot/version hashes và budget evidence. Ghi artifact không qua final validation/atomic replacement; CLI baseline trả exit 0 cả khi toàn bộ câu fail.

Baseline tests dùng hand-authored SQL làm model output và chủ yếu kiểm tra shape/substring; chưa có oracle độc lập kiểm tra result semantics. Vì R04, decoding_defects cũng có thể thấp giả tạo khi lỗi được retry thành công. Ngoài ra `run_sql_baseline`/CLI `ask --bundle` không truyền custom bundle vào `build_agent`, nên guard lấy classification từ default bundle.

Cần hai runner/mode rõ ràng, independent answer fixtures, giữ recovered faults, validate đủ mười unique IDs và ít nhất một success, fresh receipts/hashes và atomic output. Spec 010 phải reject reference artifacts. Trace: FR-703d, FR-723–725; AC-708/710.

### R10 — P1, spec-only: IR mới thiếu tham chiếu output của node trước

Vị trí: `specs/008-text-to-sql-agent.md:278`, `specs/008-text-to-sql-agent.md:357`, `specs/008-text-to-sql-agent.md:377`; plan Task 2/5/6.

IRExpression chỉ có ColumnExpression trỏ tới physical `ColumnRef(table_id, column)` và các expression khác, không có node-output/alias reference. AggregateNode có thể tạo `monthly_volume`, WindowNode có thể tạo `previous_volume`, nhưng ProjectNode không có expression hợp lệ để tham chiếu hai output này và tính growth. Gán chúng vào ColumnRef sẽ vi phạm exact snapshot membership. Đây là khoảng trống của chính spec, cần xử lý trước khi triển khai compiler.

Cần một typed output reference với node/input scope, alias hoặc stable output ID; quy định lineage/type resolution, availability, shadowing và window passthrough. Thêm một IR JSON hoàn chỉnh cho Scan → Aggregate → Window → Project → Sort/Limit, chứng minh biểu diễn được AC-703 và compile/execute ra kết quả đúng. Không tự nới physical ColumnRef để chứa alias.

### R11 — P2, spec-only: Contract mới chưa tự chứa và còn điểm cần làm rõ

Vị trí: `specs/008-text-to-sql-agent.md:747`, `specs/008-text-to-sql-agent.md:219`, `specs/008-text-to-sql-agent.md:274`.

Nhiều kiểu quan trọng như GroundingNeed, DirectionMappingAssumption, OutputLineage và receipts được mô tả là “retain ... previous contract revision”, nhưng không có định nghĩa đầy đủ trong current production models. Cần đưa đầy đủ schema vào baseline/versioned reference, tránh implementation phải tự đoán.

`StrictFrozenModel` chỉ đóng băng lớp ngoài: snapshot chứa `ColumnRef` và `RankedResult` mutable. Cần deep immutability hoặc defensive copying/revalidation rõ ràng. Hash canonicalization cũng cần quy tắc sắp xếp nội dung frozenset, không chỉ JSON object keys, và test khác `PYTHONHASHSEED`.

Egress spec cho phép nguyên canonical question nhưng đồng thời cấm mọi source value/secret: nếu người dùng nhập trực tiếp một giá trị nhạy cảm trong question, cần một quy tắc ingress/redaction hoặc refusal rõ ràng, cùng quy tắc giữ span mapping. Không thể suy ra “canonicalized” đồng nghĩa đã sạch dữ liệu nhạy cảm.

### R12 — P1, spec-only: Spec 009 có bảo đảm số liệu và aggregate safety không đúng

Vị trí: `specs/009-insight-report-agent.md:46`, `specs/009-insight-report-agent.md:63`.

FR-802c tuyên bố aggregate an toàn vì không khôi phục được individual value. Điều này sai với singleton AVG/SUM, MIN/MAX và collections; trái lineage/disclosure rules của spec 008 mới. FR-808 cho phép number words và nói chúng không thể sai giá trị: “three branches” vẫn có thể sai dù không chứa digit. Placeholder có thật cũng không chứng minh label/measure/date/comparison được diễn giải đúng.

Cần dùng validated OkResponse + lineage/disclosures; không suy classification từ plan.columns hoặc coi mọi aggregate là an toàn. Để giữ AC-800 mạnh, dùng typed factual assertions/templates và kiểm tra measure/unit/scope; hoặc thu hẹp phát biểu bảo đảm xuống numeric literal substitution và công khai giới hạn semantic correctness. Thêm adversarial tests number words, wrong fact association, singleton aggregate và sensitive derived fact. Agent này chưa có implementation để kiểm thử runtime.

### R13 — P2: Specs 009/010 và index chưa migrate theo Option B; hai workstream chưa triển khai

Vị trí: `specs/README.md:31`, `specs/009-insight-report-agent.md:35`, `specs/010-verified-corpus-and-adaptation.md:75`.

Spec 009 vẫn nhận question + QueryPlan + GroundingResponse + QueryResult, trong khi FR-725 yêu cầu chỉ nhận OkResponse và lineage/disclosures. FR-813 yêu cầu copy used_grounding_ids từ response nhưng input contract không mang chính response đó. Non-Goals vẫn ghi không dùng cloud runtime dù FR-819 yêu cầu hosted provider.

Spec 010 FR-922 vẫn inject `(question, plan, sql)` vào hai stage; FR-925 training hai task cũ. Điều này trái contract IR và provider boundary không nhận raw SQL của 008. Cần thiết kế lại corpus/conditioned tasks, canonical spans sau paraphrase, routing evidence và baseline gate. Nếu muốn thêm exemplar vào envelope, phải có extension được định nghĩa rõ trong 008; không lách guard bằng chuỗi prompt.

Index vừa nói execution đã vào scope vừa giữ shared constraint “Text-to-SQL execution is out of scope”; authorization scope cũng đã vào 008 dù index nói authorization ngoài scope. Cần tách authentication/policy administration ngoài scope với enforcement trong scope.

Không tìm thấy implementation/test modules được 009/010 yêu cầu: insight/facts/report_check, template generation/corpus verification/split/paraphrase/exemplars/adaptation. Đây là backlog chưa triển khai, không phải regression. Cần ghi trạng thái Planned rõ ràng, không công bố toàn bộ Phase 2 delivered.

### R14 — P2: Dependency và regression tests chưa khóa compatibility baseline mới

Vị trí: `pyproject.toml:12`, `tests/test_selfcheck_sql.py:97`, `tests/test_text2sql_agent.py:82`.

Spec/plan yêu cầu exact DuckDB/sqlglot pins; pyproject còn `duckdb>=1.0`, `sqlglot>=25.0`. Tests vẫn đòi hai semantic stages, cho phép mọi confidential AVG không contributor guard, và reject mọi set operation. Chúng không chứng minh IR path theo contract mới. Hosted provider tests dùng HTTP mocks, chưa là capability evidence cho organizer endpoint.

Cần pin theo compatibility checks và chuyển tests sang requirement matrix mới; giữ Phase 1 regression. Không thay expected value chỉ để test xanh: thêm failure-first tests chứng minh các lỗ hổng ở trên đã được khắc phục.

## Coverage tổng hợp

| Phần | Hiện trạng | Việc cần làm |
|---|---|---|
| 008 runtime foundation | Loader/provider prototype có chạy | Atomic load, receipts, typed guarded gateway, error domains |
| 008 scope/snapshot/contracts | Contract cũ | Authorization-first resolver, canonicalization, immutable snapshot, strict unions |
| 008 IR/routing/compiler | Chưa có đường Option B | Sửa R10/R11 rồi thực hiện plan Tasks 2–7 |
| 008 SQL/policy gates | Có checker nhưng bypass đã tái hiện | Containment, lineage, metric root, warning controls |
| 008 cache/budgets | Chưa có scoped IR cache/global budget | Plan Task 8, không tái sử dụng SQL/prompt cassette làm IR cache |
| 008 engine/executor | Read-only + fetch cap + execution timer | Mandatory bounded EXPLAIN, parameters, synchronized cleanup/resource limits |
| 008 evaluation | Reference fixture chạy; chưa đạt live evidence | Tách modes, independent semantic oracles, evidence validation |
| 009 | Spec, chưa có module triển khai | Cập nhật contract và bảo đảm số liệu trước implementation |
| 010 | Spec, chưa có module triển khai | Cập nhật IR corpus/egress/baseline gate; adaptation vẫn conditional |

## Verification evidence

- `.venv/bin/python -m pytest -q`: **85 passed in 6.01s**.
- Chạy lại toàn suite trong Python wrapper bỏ `OPENAI_API_KEY`, `CEREBRO_API_KEY`, `CEREBRO_MODEL` và chặn `socket.socket.connect/connect_ex` cho AF_INET/AF_INET6: **85 passed in 5.82s**. Đây là kiểm tra kết nối mạng ở Python process, không phải OS-level network isolation.
- Các probe dùng ScriptedProvider, DuckDB/CSV giả trong TemporaryDirectory; kết quả:

| Probe | Kết quả quan sát |
|---|---|
| Bảng ngoài grounding, end-to-end | `ok`, trả `SYNTHETIC_ONLY` |
| Sai join, wildcard sensitive, list(name), MIN(name), CURRENT_DATE, external scan | `check_sql` trả `[]` |
| External CSV trên read-only executor | Đọc được `SYNTHETIC_EXTERNAL` |
| Governed formula chỉ ở dead CTE | `check_sql` trả `[]` |
| Agent không validator/executor | `ok`, `result=None` |
| Refused response kèm SQL/result | Pydantic chấp nhận |
| Execution exception chứa sentinel | Có trong retry prompt; ba provider calls; cuối cùng `ok`, `violations=[]` |
| ProviderUnavailable | Thoát khỏi `agent.run` |
| Late missing CSV | Target hash đổi, accounts ID từ 999 thành 1 |

Các probe xác nhận hành vi lỗi đang tồn tại; không phải acceptance tests chứng minh Stage 2 đạt. Không gọi live API, không xác minh real-data manifests/receipts, không thực hiện live baseline. Không chạy build/lint/type checks vì deliverable chỉ là báo cáo; không có production change để certify.

## Thứ tự chỉnh sửa đề xuất

1. Chốt các khoảng trống IR/schema và đồng bộ 008/009/010/index; giữ Option B làm baseline, đánh dấu code hai-stage hiện tại là legacy prototype.
2. Thêm regression tái hiện R01–R09; ưu tiên atomic loader và ngăn SQL ngoài authorization/disclosure boundary trên bất kỳ đường thực thi nào còn sử dụng.
3. Triển khai Tasks 0–7: compatibility, source receipts, strict contracts, resolver, guarded provider, IR checker/router, literal resolver/compiler, AST/lineage gates.
4. Triển khai Tasks 8–11: cache/budgets, mandatory bounded engine/executor, orchestration, independent offline reference + CLI.
5. Chỉ công nhận live baseline sau Task 12 đủ receipt/evidence; sau đó mới triển khai 009 và corpus 010 theo contract đã cập nhật. Fine-tuning phụ thuộc organizer capability.

## Compliance của baseline được review

| Control | Kết quả | Evidence/ý nghĩa |
|---|---|---|
| C-01/C-02 | FAIL | Spec có nhưng R10–R13 còn thiếu/mâu thuẫn; chặn công nhận baseline hoàn chỉnh |
| C-03/C-04/C-05 | FAIL | Test design mới có; implementation/tests vẫn theo contract cũ |
| C-06 | PASS trong phạm vi suite hiện có | 85 tests pass; không thay thế acceptance mới |
| C-07 | NOT VERIFIED | Không chạy static/build checks trong review |
| C-08/C-09 | FAIL | Invalid response combinations, containment/disclosure/egress bypass đã tái hiện |
| C-10 | PASS cho review | Commands, probes và giới hạn evidence được ghi rõ |
| C-11 | PASS cho review | Chỉ thêm báo cáo, giữ nguyên production và user changes |
| C-12 | FAIL cho baseline | Downstream specs/index chưa cập nhật |

**Status: NON-COMPLIANT** — đánh giá Stage 2 so với baseline hiện tại, không phải tuyên bố đã triển khai bản sửa. Không có exception được dùng để bỏ qua các gate. Review hoàn tất; implementation remediation và live verification vẫn còn phải thực hiện.
