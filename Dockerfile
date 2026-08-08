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
# --no-proxy-headers is NOT optional here, despite looking like it.
#
# Uvicorn enables proxy-header handling BY DEFAULT and rewrites
# request.client.host from X-Forwarded-For whenever the connection comes from an
# address in --forwarded-allow-ips. app/client_ip.py treats client.host as the
# trustworthy fallback - "the direct TCP peer" - so with uvicorn also
# interpreting the header there are two layers deciding, and ours can end up
# falling back to a value the caller supplied.
#
# That is latent rather than theoretical: --forwarded-allow-ips='*' is the advice
# usually given for running behind a managed load balancer, and setting it would
# make our safe default (TRUSTED_PROXY_COUNT=0) resolve to an attacker-controlled
# address. Turning uvicorn's handling off makes client.host genuinely the peer
# everywhere, so TRUSTED_PROXY_COUNT is the single place this is decided.
#
# Verified: with this flag a request carrying a forged X-Forwarded-For resolves to
# 127.0.0.1; without it, to the forged value.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --no-proxy-headers
