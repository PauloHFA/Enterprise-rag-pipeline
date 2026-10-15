FROM postgres:15-bookworm AS pgtools
FROM python:3.11-slim AS runtime

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
RUN useradd --uid 10001 --create-home rag

# Install CPU wheels before resolving sentence-transformers dependencies.
RUN pip install --no-cache-dir torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.1

COPY api ./api
COPY shared ./shared

COPY migrations ./migrations
COPY alembic.ini .
COPY --from=pgtools /usr/lib/postgresql/15/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=pgtools /usr/lib/postgresql/15/bin/pg_restore /usr/local/bin/pg_restore

RUN mkdir -p /models && chown -R rag:rag /models
USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS instrumented
USER 0:0
RUN pip install --no-cache-dir coverage==7.16.2
COPY .coveragerc.container /app/.coveragerc
COPY tests/coverage-startup.pth /usr/local/lib/python3.11/site-packages/coverage-startup.pth
USER 10001:10001

FROM runtime AS production
