FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY demo ./demo
COPY alembic.ini .

RUN useradd --system --create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /srv/app
USER app

EXPOSE 8000

# Listens on $PORT when the platform sets one, falling back to 8000 for
# docker-compose and local runs.
#
# Cloud Run INJECTS $PORT (8080 by default) and requires the container to bind
# it - a hardcoded port means the revision never passes its health check and
# serves no traffic at all, with nothing in the application logs to explain why.
# The shell form is deliberate: exec form can't expand a variable.
#
# `exec` so uvicorn is PID 1 and receives SIGTERM directly. Without it the shell
# is PID 1, the signal never reaches uvicorn, and every deploy waits out the
# platform's grace period before being killed - which on Cloud Run drops
# in-flight requests during a rollout.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
