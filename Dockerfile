# syntax=docker/dockerfile:1

# ---- Stage 1: build the frontend (React + Vite) ----
FROM node:20-alpine AS frontend-build
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: derive the build version from git (see SHARED.md's
# versioning scheme): "<major from nearest vN tag>.<commits since that
# tag>+g<hash>". A deliberate major bump is: git tag vN && git push origin vN.
# Degrades to "0.0+gnogit" when this repo has no .git yet (not a
# requirement to build/run this app — just means the version string isn't
# meaningful until the project is put under git).
FROM python:3.12-slim AS gitinfo
WORKDIR /src
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY . .
RUN if [ -d .git ]; then \
        TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0"); \
        BASE=${TAG#v}; \
        COUNT=$(git rev-list --count "${TAG}..HEAD" 2>/dev/null || git rev-list --count HEAD 2>/dev/null || echo 0); \
        HASH=$(git rev-parse --short=12 HEAD 2>/dev/null || echo "nogit"); \
    else \
        BASE=0; COUNT=0; HASH=nogit; \
    fi \
    && echo -n "${BASE}.${COUNT}+g${HASH}" > /version.txt

# ---- Stage: bundle agent/ + agentcore/ source for the one-command agent
# installer (routers/agent_install.py serves this, unauthenticated — it's
# just source code, no secrets) — built once here, not regenerated on
# every request.
FROM python:3.12-slim AS agent-bundle
WORKDIR /bundle
COPY agent/ ./agent/
COPY agentcore/ ./agentcore/
RUN rm -rf agent/data agent/.env \
    && find . -name "__pycache__" -type d -prune -exec rm -rf {} + \
    && tar -czf /agent-bundle.tar.gz agent agentcore

# ---- Stage 3: python runtime ----
FROM python:3.12-slim AS runtime
WORKDIR /app

# git: pyproject.toml depends on fireauth straight from its GitHub repo
# (git+https://...) — pip needs the actual git binary for a VCS dependency.
# curl: fetches rclone's official install script (kept off the final
# image's PATH-affecting footprint otherwise — rclone itself is a single
# static binary at /usr/bin/rclone once installed).
RUN apt-get update && apt-get install -y --no-install-recommends git curl unzip ca-certificates \
    && curl https://rclone.org/install.sh | bash \
    && apt-get purge -y curl unzip \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY --from=gitinfo /version.txt /version.txt
COPY --from=agent-bundle /agent-bundle.tar.gz /app/agent-bundle.tar.gz
COPY backend/ ./
# `-e` (editable), not a regular install: a regular `pip install .` copies
# app/ into site-packages, which would leave app.main's __file__ pointing
# there — NOT at the ./app/static this stage populates two lines down — so
# the built frontend would silently 404. Editable keeps imports resolved
# against this same source tree.
RUN sed -i "s/^version = .*/version = \"$(cat /version.txt)\"/" pyproject.toml \
    && pip install --no-cache-dir -e .

# Built frontend assets are served directly by FastAPI (app/main.py mounts
# ./app/static/assets and serves ./app/static/index.html for "/") — no
# separate frontend server/container.
COPY --from=frontend-build /fe/dist ./app/static

ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
