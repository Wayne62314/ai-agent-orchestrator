FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/Wayne62314/ai-agent-orchestrator" \
    org.opencontainers.image.description="Resumable event-driven AI agent orchestrator"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ORCHESTRATOR_DB=/data/state.db

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY deploy/container_entrypoint.py /usr/local/libexec/container_entrypoint.py

RUN python -m pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 orchestrator \
    && mkdir --parents /data \
    && chown orchestrator:orchestrator /data \
    && chmod 0555 /usr/local/libexec/container_entrypoint.py

USER orchestrator

VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]

ENTRYPOINT ["python", "/usr/local/libexec/container_entrypoint.py"]
CMD ["agent-orchestrator", "serve", "--host", "0.0.0.0", "--port", "8080"]
