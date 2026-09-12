# syntax=docker/dockerfile:1
#
# Build context is the REPO ROOT (see docker-compose.yml's `context: ..`),
# not this directory — needed so this can COPY the sibling ../agentcore/
# package. Deploying this agent on a remote host means having both
# agent/ and agentcore/ present there (e.g. a checkout of the whole
# repo), same as how the main server's own deployment already assumes
# the whole repo is checked out, not just backend/.
FROM python:3.12-slim AS runtime
WORKDIR /app

COPY agentcore/ /agentcore/
RUN pip install --no-cache-dir /agentcore

COPY agent/ /app/
RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1

CMD ["containersafe-agent"]
