# ORCA-Prompt API + web UI. The LLM runs in a separate Ollama container (see docker-compose.yml).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY orca ./orca
COPY data/knowledge ./data/knowledge

# Build the demo database at image build time, then drop root.
RUN python -m orca.data.generate --out /app/data/orca_demo.sqlite \
    && useradd --system --uid 10001 --home /app orca \
    && mkdir -p /app/var \
    && chown -R orca:orca /app/var

USER orca

ENV ORCA_DATA_DB=/app/data/orca_demo.sqlite \
    ORCA_RUNS_DB=/app/var/runs.sqlite \
    ORCA_KNOWLEDGE_DIR=/app/data/knowledge \
    ORCA_OLLAMA_BASE_URL=http://ollama:11434

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1

CMD ["uvicorn", "orca.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-keep-alive", "75"]
