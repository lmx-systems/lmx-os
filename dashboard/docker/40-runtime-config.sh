#!/bin/sh
# Regenerates runtime-config.js from environment variables at container
# start (roadmap item D2) - nginx:alpine runs everything in
# /docker-entrypoint.d/ before serving. This is what makes API_BASE_URL a
# `docker run -e` knob instead of a bake-it-in-at-build-time decision.
set -eu

cat > /usr/share/nginx/html/runtime-config.js <<EOF
window.__LMX_RUNTIME_CONFIG__ = {
  API_BASE_URL: "${API_BASE_URL:-}",
  API_SHARED_SECRET: "${API_SHARED_SECRET:-}"
};
EOF
