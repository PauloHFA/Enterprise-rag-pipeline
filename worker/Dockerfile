FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The worker will be started via a command in docker-compose, but we can set a default if needed.
# We'll use an entrypoint script or just run the worker module.
# For now, we'll assume the worker is started by a command like: python worker.py
# We'll create the worker.py file next.

# We don't expose a port because the worker is a background service.