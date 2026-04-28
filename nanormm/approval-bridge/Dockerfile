FROM python:3.11.8-slim-bookworm

# Install runtime libs needed by psycopg (the binary wheel pulls in libpq).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Build context is `nanormm/`, so these are sibling dirs of the parent.
COPY trmm-mcp/ /app/trmm-mcp/
COPY approval-bridge/ /app/approval-bridge/

WORKDIR /app/approval-bridge

RUN pip install --no-cache-dir -e /app/trmm-mcp \
    && pip install --no-cache-dir -e /app/approval-bridge

EXPOSE 8000

# Healthcheck: hit /healthz (no auth needed)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"

CMD ["python", "-m", "approval_bridge"]
