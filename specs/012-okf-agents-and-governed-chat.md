# 012 — OKF Agents and Governed Database Chat

## Goal

Turn the read-only semantic prototype into a bounded local workflow that can generate a candidate OKF bundle, explicitly activate it, and answer conversational database questions through validated DuckDB SQL.

## Functional Requirements

- FR-701: Configure an OpenAI-compatible provider ID, display name, chat-completions endpoint, model, and key through server-side environment variables without code changes or browser-visible secrets.
- FR-702: Run typed definition, relationship, and metric/policy stages using catalog metadata only.
- FR-703: Preserve declared joins, reject proposed endpoints outside the catalog, and record generation provenance and confidence.
- FR-704: Compile proposals into a separate Google-OKF-compatible candidate and validate it before explicit activation.
- FR-705: Ground each chat turn in the active semantic bundle before planning or generating SQL.
- FR-706: Accept only one read-only DuckDB query over approved tables and columns; block wildcard selection, restricted fields, raw confidential fields, external access, and writes.
- FR-707: Limit execution to 100 returned rows and 10 seconds, with at most one model repair attempt.
- FR-708: Return the answer, SQL, result table, semantic version, evidence, warnings, and redacted agent trace to CLI and web clients.
- FR-709: Keep at most ten client-supplied conversation turns and do not persist chat history server-side.

## Acceptance Criteria

- A credential-free run scans the source, creates a structural candidate containing all declared relationships, and validates without modifying the golden bundle.
- A mocked compatible endpoint exercises strict JSON Schema output and the one-time JSON-object compatibility fallback when the format is rejected or its response fails typed validation.
- A compatible endpoint may wrap an otherwise valid JSON object in one Markdown code fence; the gateway normalizes that wrapper before schema validation.
- Automated tests do not load or call a live provider from a developer's local `.env`.
- The development launcher prefers the repository `.venv` interpreter and exits before starting Vite when the API cannot start.
- Invalid or hallucinated relationships cannot compile or activate.
- Safe aggregate SQL executes against a read-only fixture; writes, raw PII, raw confidential values, wildcard queries, unknown objects, and external-access functions are blocked.
- The Text-to-SQL workspace shows runtime readiness and supports a conversation with disclosed SQL, results, evidence, warnings, and trace.
- Changing provider identity, endpoint, model, or key requires only `.env` changes and a server restart.
- Runtime status exposes the provider ID and display name but never the API key.

## Boundaries

DuckDB and local single-user operation are the only execution target. Authentication, durable conversation storage, remote deployment, and native non-OpenAI protocols remain out of scope.
