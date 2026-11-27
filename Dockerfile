# YardMate hosted demo: builds the web screens, then runs the Python server.
FROM node:22-bookworm-slim AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
RUN uv sync --frozen --no-dev
COPY --from=web /app/frontend/dist ./frontend/dist
RUN useradd --create-home yardmate && mkdir -p /var/yardmate && chown yardmate /var/yardmate
USER yardmate
ENV SPEC_AGENT_DATA_DIR=/var/yardmate/data \
    SPEC_AGENT_WORKSPACE_DIR=/var/yardmate/workspace \
    SPEC_AGENT_COOKIE_SECURE=true \
    SPEC_AGENT_BEHIND_PROXY=true \
    PORT=8000
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "spec-agent", "serve", "--host", "0.0.0.0"]
