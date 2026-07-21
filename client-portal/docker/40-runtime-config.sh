#!/bin/sh
# Regenerates runtime-config.js from environment variables at container
# start (roadmap item D2) - nginx:alpine runs everything in
# /docker-entrypoint.d/ before serving. No API_SHARED_SECRET here, unlike
# dashboard/ - the client portal uses per-client JWT auth, never the
# internal ops shared secret.
set -eu

cat > /usr/share/nginx/html/runtime-config.js <<EOF
window.__LMX_RUNTIME_CONFIG__ = {
  API_BASE_URL: "${API_BASE_URL:-}"
};
EOF
