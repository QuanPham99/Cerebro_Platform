# Định nghĩa Semantic Layer của Cerebro

> **Trạng thái:** Baseline thiết kế cuối cùng cho prototype
>
> **Phạm vi:** Nền tảng semantic cho ngân hàng bán lẻ PostgreSQL, được grounding bằng Google OKF v0.2

## Semantic layer là gì

Semantic layer là lớp chuyển đổi được quản trị giữa cấu trúc vật lý của cơ sở dữ liệu và ngôn ngữ nghiệp vụ.

Cơ sở dữ liệu có thể cho chúng ta biết `transactions.amount` là một column kiểu số, được kết nối với một account thông qua `account_id`. Tuy nhiên, bản thân cơ sở dữ liệu không giải thích:

- Amount là số có dấu hay luôn dương.
- Credit và debit cần được diễn giải như thế nào.
- “Customer” là một cá nhân, một pháp nhân hay một account holder.
- Join path nào tránh làm trùng lặp transaction.
- Balance là số dư hiện tại, cuối ngày hay số dư trung bình.
- Field nào chứa thông tin cá nhân hoặc tài chính.
- Một định nghĩa được tạo tự động, đã được rà soát hay đã lỗi thời.

Semantic layer ghi lại những ý nghĩa đó dưới dạng một hợp đồng rõ ràng:

```text
Câu hỏi nghiệp vụ
       |
       v
Khái niệm và định nghĩa nghiệp vụ
       |
       v
Metric, grain, dimension và policy
       |
       v
Quan hệ và join path được phê duyệt
       |
       v
Bảng và column PostgreSQL vật lý
```

Đối với Cerebro, semantic layer không chỉ là một graph trực quan hay một tập hợp mô tả do AI tạo ra. Đây là tri thức có phiên bản, cho agent biết:

1. Dữ liệu có ý nghĩa gì.
2. Ý nghĩa đó tồn tại ở đâu trong cấu trúc vật lý.
3. Dữ liệu có thể được join an toàn như thế nào.
4. Những giả định và hạn chế nào được áp dụng.
5. Vì sao thông tin này đáng tin cậy.

## Google Open Knowledge Format cung cấp những gì

Cerebro được xây dựng trên [repository Open Knowledge Format](https://github.com/GoogleCloudPlatform/open-knowledge-format) do Google duy trì và [đặc tả OKF v0.2](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md).

Một OKF bundle là một thư mục gồm các tài liệu Markdown có YAML frontmatter. Google OKF cho phép cấu trúc thư mục theo từng domain, vì vậy Cerebro tổ chức banking bundle được tạo ra như sau:

```text
knowledge/bank-demo/
├── index.md
├── datasets/
│   ├── index.md
│   └── retail_bank.md
├── tables/
│   ├── index.md
│   ├── customers.md
│   ├── accounts.md
│   ├── account_holders.md
│   └── transactions.md
├── concepts/
│   ├── index.md
│   ├── customer.md
│   ├── account.md
│   └── transaction.md
├── relationships/
│   ├── index.md
│   ├── customer_account_ownership.md
│   └── account_transactions.md
├── metrics/
│   ├── index.md
│   └── outgoing_transaction_volume.md
└── policies/
    ├── index.md
    └── restricted_customer_data.md
```

Taxonomy thư mục là quy ước của Cerebro; các tài liệu bên trong vẫn là những concept hợp lệ theo OKF v0.2.

| Thư mục | Mục đích semantic |
| --- | --- |
| `datasets/` | Tri thức ở cấp data source và schema |
| `tables/` | Bảng PostgreSQL vật lý, column, key và grain |
| `concepts/` | Ý nghĩa nghiệp vụ độc lập với tên bảng vật lý |
| `relationships/` | Cardinality được phê duyệt và SQL join path có thể thực thi |
| `metrics/` | Phép tính, filter, grain và quy tắc aggregation được quản trị |
| `policies/` | Phân loại độ nhạy cảm và hạn chế sử dụng |

Bundle root là điểm vào theo nguyên tắc progressive disclosure dành cho con người và agent:

```markdown
---
okf_version: "0.2"
---

# Retail Banking Knowledge Bundle

Tri thức semantic được tạo từ nguồn PostgreSQL `bank-demo`.

## Nội dung

- [Retail bank dataset](datasets/retail_bank.md)
- [Bảng vật lý](tables/index.md)
- [Khái niệm nghiệp vụ](concepts/index.md)
- [Quan hệ được phê duyệt](relationships/index.md)
- [Metric](metrics/index.md)
- [Policy](policies/index.md)
```

Một table concept đơn giản có thể có dạng như sau:

```markdown
---
type: PostgreSQL Table
title: Accounts
resource: postgresql://bank/public/accounts
status: stable
tags: [retail-banking, accounts]
generated:
  by: cerebro/openai
  at: 2026-08-26T10:00:00Z
verified:
  - by: human:reviewer
    at: 2026-08-26T11:00:00Z
---

# Accounts

Mỗi row đại diện cho một tài khoản ngân hàng bán lẻ.

Một account có thể có nhiều holder thông qua
[Account Ownership](/relationships/account_ownership.md).
```

Các liên kết Markdown tiêu chuẩn tạo kết nối graph giữa các concept. OKF v0.2 cũng định nghĩa các field cho provenance, verification, trust, freshness và lifecycle, đồng thời chủ ý không quy định một cơ sở dữ liệu, retrieval engine hay agent runtime cụ thể.

YAML frontmatter OKF thông thường và phần nội dung Markdown bảo đảm tính di động. Block frontmatter `cerebro` bổ sung của Cerebro cung cấp các chi tiết grounding SQL mang tính xác định, trong khi vẫn tương thích với định dạng này.

Reference implementation của Google hiện cung cấp:

- Một agent tạo OKF từ metadata BigQuery.
- Khả năng enrichment từ web theo lựa chọn.
- Parse OKF và tạo bundle.
- Các sample bundle và validation test.
- Một static graph viewer dựa trên Cytoscape.
- Xử lý metadata về trust và freshness.

Reference implementation này không cung cấp:

- PostgreSQL semantic scanner.
- Business semantic model hoàn chỉnh.
- Hợp đồng SQL join có kiểu rõ ràng.
- Hybrid semantic retrieval.
- Text-to-SQL retrieval agent.
- Ứng dụng web để review và publish.
- Knowledge-serving API cho production.

Cerebro bổ sung các khả năng này xung quanh hợp đồng OKF có tính di động.

## Quyền sở hữu bốn layer: Google và Cerebro

Google cung cấp nền tảng OKF cùng các công cụ proof-of-concept để tạo và trực quan hóa dữ liệu. Google không cung cấp một hệ thống semantic bốn layer hoàn chỉnh.

| Layer | Google hiện cung cấp | Cerebro phải phát triển |
| --- | --- | --- |
| **1. Physical** | Abstraction cho nguồn BigQuery, đọc metadata, liệt kê concept và tùy chọn lấy mẫu row | Quét PostgreSQL, snapshot được chuẩn hóa và trích xuất PK/FK theo cách xác định |
| **2. Business concepts** | Enrichment bằng Gemini/Google ADK để ghi tài liệu OKF tổng quát từ metadata BigQuery và nguồn web tùy chọn | Concept ngân hàng, alias, classification, tích hợp OpenAI và human review |
| **3. Query semantics** | Tài liệu OKF có thể mở rộng, liên kết Markdown, provenance, trust và các field lifecycle | Grain, dimension, measure, hợp đồng join, cardinality, warning và validator có cấu trúc |
| **4. Retrieval** | Static Cytoscape viewer, tìm kiếm cơ bản theo title/ID/tag, lọc theo type và backlink | Hybrid search, mở rộng graph có kiểu, phiên bản đang hoạt động, policy filtering, MCP tool và grounding response |

### Những gì Cerebro tái sử dụng trực tiếp

- Quy ước tài liệu và bundle của OKF v0.2.
- Markdown với YAML frontmatter.
- Concept path và điều hướng progressive disclosure bằng `index.md`.
- Các field tiêu chuẩn về provenance, generation, verification, trust, freshness và lifecycle.
- Parse và ghi bundle, xử lý path cùng các validation test liên quan.
- Trích xuất graph từ liên kết Markdown và các ý tưởng từ Cytoscape viewer.
- Các example bundle làm tài liệu tham chiếu cho conformance và visualization.

### Những gì Cerebro điều chỉnh

- Thay nguồn BigQuery bằng PostgreSQL scanner đã chuẩn hóa.
- Thay cách gọi model riêng của Google bằng một interface không phụ thuộc provider, với adapter đầu tiên sử dụng OpenAI Responses API.
- Thay prompt enrichment tổng quát bằng hướng dẫn semantic cho ngân hàng bán lẻ.
- Tắt row sampling và web enrichment trong prototype chỉ sử dụng metadata.
- Thay static viewer bằng workspace review graph dựa trên React.
- Giữ nguyên các field OKF tiêu chuẩn, đồng thời bổ sung hợp đồng query semantics trong namespace `cerebro`.

### Những gì Cerebro xây dựng mới

- Snapshot schema PostgreSQL và physical metadata model.
- Schema đề xuất cho banking concept, classification, relationship và metric.
- Validation xác định cho physical reference, grain, join và link.
- Human review, chỉnh sửa, phê duyệt, từ chối và publication.
- Hình chiếu retrieval gồm full-text, vector và typed graph.
- MCP tool trả về package grounding cho Text-to-SQL.
- Bộ câu hỏi ngân hàng chuẩn để đánh giá retrieval.

Ranh giới triển khai là:

```text
Google
  Định dạng OKF
  + reference producer BigQuery/Gemini
  + static graph viewer

Cerebro
  Nạp dữ liệu PostgreSQL
  + business semantics cho ngân hàng
  + query contract mang tính xác định
  + human review
  + hybrid retrieval
  + MCP grounding
```

## Cách Cerebro ánh xạ semantic layer

Cerebro ánh xạ tri thức qua bốn cấp độ được kết nối với nhau.

### 1. Physical layer

Physical layer được khám phá từ PostgreSQL theo cách xác định:

```text
public.customers
  customer_id UUID PK
  full_name TEXT
  date_of_birth DATE
  risk_rating TEXT

public.accounts
  account_id UUID PK
  product_code TEXT
  current_balance NUMERIC

public.account_holders
  customer_id UUID FK -> customers.customer_id
  account_id UUID FK -> accounts.account_id

public.transactions
  transaction_id UUID PK
  account_id UUID FK -> accounts.account_id
  amount NUMERIC
  direction TEXT
  booked_at TIMESTAMP
```

Scanner ghi lại database identifier, data type, primary key, foreign key, constraint và comment. LLM không được phép tự tạo ra những technical metadata này.

### 2. Business concept layer

Enrichment model đề xuất ánh xạ giữa các đối tượng vật lý và ý tưởng nghiệp vụ:

| Business concept | Physical mapping | Ý nghĩa |
| --- | --- | --- |
| Customer | `customers` | Một bên tham gia ngân hàng bán lẻ được ngân hàng công nhận |
| Account | `accounts` | Tài khoản tiền gửi hoặc tài khoản giao dịch |
| Account holder | `account_holders` | Liên kết giữa customer và account |
| Transaction | `transactions` | Một biến động đã được ghi nhận, tác động đến một account |

Các ánh xạ này vẫn là đề xuất cho đến khi được một người rà soát và phê duyệt.

### 3. Query semantics layer

Text-to-SQL cần cấu trúc mang tính xác định cao hơn so với các liên kết Markdown thông thường. Cerebro giữ mỗi tài liệu ở trạng thái OKF hợp lệ và bổ sung block frontmatter `cerebro` có namespace:

```yaml
cerebro:
  physical:
    source: bank-demo
    schema: public
    table: transactions
    snapshot: snapshot-2026-08-26

  grain:
    description: One row per booked account transaction
    key:
      - transaction_id

  dimensions:
    - column: booked_at
      semantic_type: booking_timestamp
    - column: direction
      semantic_type: transaction_direction

  measures:
    - column: amount
      aggregation: sum
      currency_column: currency_code

  relationships:
    - target: tables/accounts
      type: many_to_one
      join:
        - from: account_id
          to: account_id
      approved: true

  classifications:
    - target: account_id
      sensitivity: financial_identifier

  guidance:
    - Treat credits and debits according to the direction column.
    - Do not join customers directly to transactions.
```

Cấu trúc này tạo ra một join path được quản trị:

```text
Customer
   `-- account_holders
          `-- Account
                 `-- Transaction
```

Nó ngăn query agent tự tạo ra các join không hợp lệ như:

```sql
customers.customer_id = transactions.account_id
```

Ánh xạ cũng có thể cảnh báo rằng account đồng sở hữu có thể khiến một transaction được quy cho nhiều customer.

#### Ví dụ về business concept

`concepts/customer.md` tách ý nghĩa nghiệp vụ của customer khỏi bảng vật lý `customers`:

```markdown
---
type: Business Concept
title: Customer
description: A retail banking party recognized by the bank.
status: stable
tags: [retail-banking, party, customer]
generated:
  by: cerebro/openai
  at: 2026-08-26T10:00:00Z
verified:
  - by: human:reviewer
    at: 2026-08-26T11:00:00Z

cerebro:
  mappings:
    primary:
      table: tables/customers
      key: [customer_id]

  aliases:
    - client
    - account holder
    - retail customer

  classifications:
    - target: full_name
      sensitivity: pii
    - target: date_of_birth
      sensitivity: restricted

  relationships:
    - target: concepts/account
      via: relationships/customer_account_ownership
      type: many_to_many
---

# Customer

Customer là một cá nhân tham gia dịch vụ ngân hàng bán lẻ.

Một customer có thể sở hữu nhiều [Account](/concepts/account.md), và một account
có thể thuộc đồng sở hữu của nhiều customer.

Kết nối được phê duyệt được mô tả trong
[Customer Account Ownership](/relationships/customer_account_ownership.md).
```

Việc tách biệt này cho phép một business concept ánh xạ tới nhiều nguồn vật lý trong phiên bản tương lai mà không thay đổi cách người dùng đặt câu hỏi.

#### Ví dụ về quan hệ được phê duyệt

`relationships/customer_account_ownership.md` chuyển một kết nối graph thành hợp đồng join có thể thực thi và review:

```markdown
---
type: Semantic Relationship
title: Customer Account Ownership
description: Approved relationship connecting customers to their accounts.
status: stable
tags: [ownership, approved-join]

cerebro:
  source: tables/customers
  target: tables/accounts
  cardinality: many_to_many

  bridge:
    table: tables/account_holders

  joins:
    - left:
        table: customers
        column: customer_id
      right:
        table: account_holders
        column: customer_id

    - left:
        table: account_holders
        column: account_id
      right:
        table: accounts
        column: account_id

  warnings:
    - Joint accounts can cause one transaction to be attributed to multiple customers.
---

# Customer Account Ownership

Customer và account có quan hệ many-to-many thông qua
[bảng Account Holders](/tables/account_holders.md).
```

#### Ví dụ về metric đơn giản

`metrics/outgoing_transaction_volume.md` minh họa cách một phép tính được quản trị liên kết ngôn ngữ nghiệp vụ với hành vi truy vấn:

```markdown
---
type: Metric
title: Outgoing Transaction Volume
description: Total value of booked debit transactions.
status: stable
tags: [transaction, debit, volume]

cerebro:
  source: tables/transactions
  expression: SUM(transactions.amount)
  grain: Query-dependent
  filter:
    column: transactions.direction
    operator: equals
    value: DEBIT
  time_column: transactions.booked_at
  allowed_dimensions:
    - concepts/customer
    - concepts/account
  warnings:
    - Customer-level grouping can duplicate values for jointly owned accounts.
---

# Outgoing Transaction Volume

Tổng amount của các transaction có `direction = 'DEBIT'`.

Sử dụng [Customer Account Ownership](/relationships/customer_account_ownership.md)
khi phân tích metric này theo customer.
```

Đây là metric minh họa đơn giản. Việc author và validate metric phức tạp nằm ngoài phạm vi prototype năm ngày.

### 4. Retrieval layer

Các OKF concept đã publish được chiếu thành ba retrieval view:

- PostgreSQL full-text search cho identifier chính xác, alias và thuật ngữ ngân hàng.
- Embedding pgvector cho semantic similarity.
- Typed graph edge cho join được phê duyệt và business concept có liên kết.

Với câu hỏi:

> Những customer nào có tổng giá trị transaction chuyển ra lớn nhất?

MCP retrieval tool của Cerebro cần trả về một grounding package tương tự:

```json
{
  "semantic_version": "bank-2026-08-26-01",
  "concepts": [
    "concepts/customer",
    "concepts/account",
    "concepts/transaction"
  ],
  "tables": [
    "tables/customers",
    "tables/account_holders",
    "tables/accounts",
    "tables/transactions"
  ],
  "join_path": [
    "customers.customer_id = account_holders.customer_id",
    "account_holders.account_id = accounts.account_id",
    "accounts.account_id = transactions.account_id"
  ],
  "filters": [
    "transactions.direction = 'DEBIT'"
  ],
  "grain": "customer",
  "warnings": [
    "Joint accounts can attribute one transaction to multiple customers."
  ],
  "provenance": [
    "snapshot-2026-08-26",
    "human-reviewed"
  ]
}
```

Nhóm Text-to-SQL sử dụng grounding package này và tạo SQL từ đó. Agent của nhóm không cần parse toàn bộ knowledge graph hay đoán ý nghĩa của relationship.

## Phân bổ agent

Cerebro không nên tạo một agent cho mỗi layer. Layer 1 và 4 là trách nhiệm của hệ thống mang tính xác định; chỉ Layer 2 và 3 cần phán đoán semantic.

| Layer | Component | Agent LLM? | Lý do |
| --- | --- | --- | --- |
| Physical | `PostgreSQLScanner` | Không | Database metadata là ground truth và không được tự tạo |
| Business concepts | `SemanticEnrichmentAgent` | Có | Definition, alias và conceptual mapping cần phán đoán semantic |
| Query semantics | `SemanticEnrichmentAgent` cộng với `OKFValidator` | Một phần | Agent đề xuất grain và guidance; code xác định kiểm tra các khẳng định vật lý |
| Retrieval | `SemanticRetriever` và MCP server | Không | Ranking, graph traversal, filtering và dựng response phải có khả năng tái lập |

Trong prototype, một semantic-enrichment agent có phạm vi giới hạn xử lý hai giai đoạn có cấu trúc:

```text
Giai đoạn 1: Enrichment nghiệp vụ
  concept
  definition
  alias
  mục đích của table
  classification

Giai đoạn 2: Enrichment truy vấn
  grain
  dimension và measure
  join được suy ra từ key đã khám phá
  query guidance
  cảnh báo về ambiguity và fan-out
```

Hai giai đoạn có thể dùng prompt riêng, nhưng cùng chia sẻ một workflow và một hợp đồng structured output. Agent có thể đề xuất semantic nhưng không thể trực tiếp publish chúng.

Control flow hoàn chỉnh là:

```text
PostgreSQLScanner
  metadata mang tính xác định
        |
        v
SchemaSnapshot
        |
        v
SemanticEnrichmentAgent
  đề xuất business semantics và query semantics
        |
        v
OKFValidator
  kiểm tra schema reference, join, link và field bắt buộc
        |
        v
Human reviewer
  sửa và phê duyệt ý nghĩa nghiệp vụ
        |
        v
BundlePublisher
  kích hoạt một bundle bất biến đã được review
        |
        v
SemanticRetriever + MCP
  cung cấp grounding cho nhóm Text-to-SQL
```

Phiên bản production trong tương lai có thể tách enrichment cho concept, relationship, metric và policy thành các specialist agent. Việc tách này cần dựa trên trách nhiệm semantic và nhu cầu workflow đã được đo lường, thay vì mô phỏng bốn layer kiến trúc.

## Ví dụ semantic mapping hoàn chỉnh

Toàn bộ cách diễn giải một câu hỏi nghiệp vụ có thể được lần theo từ ngôn ngữ đến input SQL vật lý:

```text
Câu hỏi:
"Những customer nào có tổng giá trị transaction chuyển ra lớn nhất?"
                         |
                         v
Metric:
Outgoing Transaction Volume
SUM(transactions.amount)
WHERE direction = 'DEBIT'
                         |
                         v
Business concept:
Customer -> Account -> Transaction
                         |
                         v
Semantic relationship được phê duyệt:
Customer <-> Account thông qua account_holders
                         |
                         v
Physical join path:
customers.customer_id
    = account_holders.customer_id

account_holders.account_id
    = accounts.account_id

accounts.account_id
    = transactions.account_id
                         |
                         v
Policy và warning:
PII của customer thuộc loại restricted.
Quyền đồng sở hữu có thể làm trùng lặp việc quy transaction cho customer.
```

Vì vậy, OKF bundle được tạo ra đồng thời là:

- Knowledge repository con người có thể đọc.
- Hợp đồng semantic máy có thể đọc.
- Knowledge graph có thể điều hướng.
- Nguồn grounding cho agent Text-to-SQL.

## Ánh xạ prototype năm ngày

Prototype chứng minh một luận điểm trung tâm:

> Một mock banking schema PostgreSQL có thể được chuyển đổi thành tri thức đã review, có thể trực quan hóa và truy xuất bằng máy, qua đó grounding đáng tin cậy cho agent Text-to-SQL.

```text
Metadata PostgreSQL
        |
        v
Quét schema mang tính xác định
        |
        v
Technical concept OKF
        |
        v
Structured enrichment bằng OpenAI
        |
        v
Định nghĩa nghiệp vụ và semantic được đề xuất
        |
        v
Human review và publication
        |
        v
Index full-text, vector và typed graph
        |
        v
Ngữ cảnh MCP grounding cho Text-to-SQL
```

Phiên bản năm ngày tập trung vào table, concept, grain, join, classification, khám phá graph và retrieval. Author metric phức tạp, tự động hóa schema change, authentication, crawl tài liệu và production governance thuộc các giai đoạn sau.
