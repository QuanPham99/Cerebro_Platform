# syntax=docker/dockerfile:1.7

FROM node:20-bookworm-slim AS web-builder

WORKDIR /build/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/index.html apps/web/tsconfig.json apps/web/tsconfig.app.json apps/web/tsconfig.node.json apps/web/vite.config.ts apps/web/vitest.config.ts ./
COPY apps/web/public ./public
COPY apps/web/src ./src
RUN npm test && npm run build


FROM python:3.12-slim-bookworm AS python-deps

COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /uvx /bin/
WORKDIR /build
COPY pyproject.toml uv.lock ./
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev --extra ai --no-install-project


FROM python:3.12-slim-bookworm AS runtime

# libpango/libpangocairo + a font package are native (non-pip) requirements of WeasyPrint,
# used by the report agent (specs/023-executive-report-agent.md) to render PDFs; without a
# font package installed, WeasyPrint has nothing to shape text with and PDFs render blank.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates curl tini \
        libpango-1.0-0 libpangocairo-1.0-0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 cerebro \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin cerebro

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CEREBRO_DATABASE_PATH=/data/workshop.duckdb

WORKDIR /app
COPY --from=python-deps /opt/venv /opt/venv
COPY --chown=10001:10001 src ./src
COPY --chown=10001:10001 config ./config
COPY --chown=10001:10001 vendor/open-knowledge-format ./vendor/open-knowledge-format
COPY --chown=10001:10001 knowledge/bank-workshop ./knowledge/bank-workshop
COPY --from=web-builder --chown=10001:10001 /build/apps/web/dist ./apps/web/dist
RUN install -d -o 10001 -g 10001 \
    /app/artifacts \
    /app/knowledge/generated \
    /app/knowledge/reviewed \
    /app/knowledge/saved_charts \
    && install -d -o 10001 -g 10001 -m 0555 /data
COPY --chown=10001:10001 data/workshop.duckdb /data/workshop.duckdb
RUN test -s /data/workshop.duckdb \
    && chmod 0444 /data/workshop.duckdb

ARG BUILD_VERSION=dev
ARG BUILD_REVISION=unknown
LABEL org.opencontainers.image.title="Cerebro Semantic Layer" \
      org.opencontainers.image.description="Governed semantic grounding, Text2SQL, MCP, and web UI" \
      org.opencontainers.image.source="https://github.com/QuanPham99/Cerebro_Platform" \
      org.opencontainers.image.version="$BUILD_VERSION" \
      org.opencontainers.image.revision="$BUILD_REVISION"

USER 10001:10001
# Default port is 8000 (used by the vServer/Compose+Caddy deployment path, see
# deploy/compose.production.yaml and deploy/Caddyfile, both fixed to 8000). GreenNode
# Agent Runtime instead requires the app on 8080 with a bare GET /health — set PORT=8080
# via the console's env vars for that target; see docs/deployment-greennode-agent-runtime.md.
ENV PORT=8000
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["sh", "-c", "curl -sf http://127.0.0.1:${PORT}/health || exit 1"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "exec python -m cerebro.cli serve --host 0.0.0.0 --port ${PORT}"]
