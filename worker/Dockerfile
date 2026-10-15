FROM python:3.11-slim AS runtime

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
RUN useradd --uid 10001 --create-home rag

RUN pip install --no-cache-dir torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu

COPY worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.1

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/*
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

COPY worker ./worker
COPY shared ./shared

RUN mkdir -p /models && chown -R rag:rag /models
USER 10001:10001

# The worker will be started via a command in docker-compose
CMD ["python", "-m", "worker.worker"]

FROM runtime AS instrumented
USER 0:0
RUN pip install --no-cache-dir coverage==7.16.2
COPY .coveragerc.container /app/.coveragerc
COPY tests/coverage-startup.pth /usr/local/lib/python3.11/site-packages/coverage-startup.pth
USER 10001:10001

FROM runtime AS production
