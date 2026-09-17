# Hướng dẫn giao diện Cerebro và OKF v0.2

Tài liệu này giải thích cách workspace Cerebro biểu diễn bundle OKF v0.2 đã
được rà soát trong `knowledge/bank-workshop`, và cách lần từ ý nghĩa nghiệp vụ
đến dữ liệu vật lý mà không chỉnh sửa hoặc kích hoạt semantic definition.

## Khởi động local

Từ thư mục gốc của repository:

```bash
./scripts/dev.sh
```

Mở <http://127.0.0.1:5173>. Dịch vụ FastAPI và MCP chạy tại
<http://127.0.0.1:8000>.

## Bố cục workspace

- Menu workspace chọn một trong bốn workspace theo vai trò — Semantic
  Constellation, Text-to-SQL agents, Report agent, và Customer self-service —
  còn rail bên trái trong Semantic Constellation có thể thu gọn độc lập. Tài
  liệu này chỉ mô tả hình chiếu graph của Semantic Constellation; customer
  workspace chiếu một tập con graph bị giới hạn (`?scope=customer`) dựa trên
  allowlist cố định trong `src/cerebro/customer_scope.py`.
- Trong semantic workspace, preset theo layer và bộ lọc theo từng kind điều
  khiển hình chiếu graph. Search tìm theo title, stable ID, mô tả và alias.
- Canvas ở giữa hỗ trợ pan, zoom, fit, reset, chọn node và điều hướng bàn phím.
  Legend nằm trong canvas và giải thích edge grammar đang dùng.
- Rail chi tiết hiển thị contract có kiểu của đối tượng được chọn và có thể thu
  gọn độc lập. Graph tự resize sau khi một trong hai rail thay đổi.

Bộ lọc và lựa chọn không sửa bundle. Candidate được giữ riêng cho đến khi
validate, human review và activation rõ ràng.

## Semantic Profile v0.1 trên OKF v0.2

Bundle dùng tài liệu OKF v0.2 cùng Cerebro Semantic Profile v0.1.
`cerebro.kind` là kind chuẩn của ứng dụng; `type` OKF gốc vẫn được giữ để tương
thích.

| Layer | Kind | Mục đích |
| --- | --- | --- |
| Physical | Dataset, Physical table | Phạm vi nguồn, column, key và grain |
| Semantic | Entity, Dimension, Relationship, Business rule | Danh tính nghiệp vụ, chiều phân tích, join và điều kiện quản trị |
| Metrics | Metric | Measure có kiểu, dependency, dimension tương thích và ngữ nghĩa thời gian |
| Governance | Policy | Classification và ràng buộc sử dụng |
| Compatibility | Legacy concept, Generic | Bundle cũ và kiểu OKF chưa biết |

Column được hiển thị trong chi tiết physical table thay vì trở thành node riêng.

## Edge grammar

| Edge | Hướng | Ý nghĩa |
| --- | --- | --- |
| `physical_fk` | Physical source đến target | Join được phê duyệt, có endpoint one/many |
| `relationship_endpoint` | Relationship đến member | Thành viên của join contract được quản trị |
| `entity_table_mapping` | Entity đến physical table | Ánh xạ danh tính vào nơi lưu trữ |
| `dimension_entity` | Dimension đến entity | Dimension dùng để phân tích entity |
| `dimension_table_mapping` | Dimension đến physical table | Ánh xạ column của dimension |
| `semantic_relationship` | Relationship giữa các entity | Hướng nghiệp vụ của join |
| `metric_entity` | Metric đến entity | Entity grain do metric quản trị |
| `metric_dimension` | Metric đến dimension | Nhóm hoặc time dimension tương thích |
| `metric_dependency` | Metric đến dependency | Đầu vào vật lý cần cho measure |
| `rule_entity` / `rule_dependency` | Rule đến đầu vào | Phạm vi của business rule |
| `policy_coverage` | Policy đến đối tượng | Phạm vi áp dụng của policy |

Cardinality vật lý luôn tách biệt với hướng semantic. Edge membership của
relationship chủ ý không có mũi tên.

## Lần theo một ví dụ

Với câu hỏi “Card fraud rate by card type”:

1. Chọn **Metrics**, sau đó chọn `metric.card-fraud-rate`.
2. Đi theo dimension tương thích đến `dimension.card-type` và entity đến
   `entity.card-transaction`.
3. Xem ratio measure có kiểu và dependency `table.card_transactions`.
4. Đi theo `relationship.card_transaction_card` đến `entity.card` và
   `table.cards` khi câu hỏi cần thuộc tính của card.
5. Xem `policy.sensitive-banking-data` trước khi sử dụng kết quả.

Dùng **Open OKF source** để so sánh đối tượng graph với YAML frontmatter.

## API tương đương

```bash
curl http://127.0.0.1:8000/api/bundles/active
curl http://127.0.0.1:8000/api/graph
curl http://127.0.0.1:8000/api/concepts/metric.card-fraud-rate
```

Graph API và grounding API chỉ cung cấp metadata. Thực thi SQL là một surface
riêng và luôn được ràng buộc bằng authorization.

Bản tiếng Anh: [ui-and-okf-guide.md](ui-and-okf-guide.md).
