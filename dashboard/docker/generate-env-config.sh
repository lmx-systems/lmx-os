#!/bin/sh
# Runs automatically at container startup - any executable script under
# /docker-entrypoint.d/ is picked up by the base nginx image's own
# entrypoint before nginx starts (see Dockerfile). Writes real runtime
# env vars into a static JS file served alongside the app bundle, so
# pointing this dashboard at a different API/shared secret is a container
# restart with different `environment:` values, not a Docker image
# rebuild (docs/ROADMAP.md D2) - see index.html/src/lib/api.ts for how
# it's consumed.
set -eu

cat <<EOF > /usr/share/nginx/html/env-config.js
window.__RUNTIME_CONFIG__ = {
  VITE_API_BASE_URL: "${DASHBOARD_API_BASE_URL:-http://localhost:8000}",
  VITE_API_SHARED_SECRET: "${API_SHARED_SECRET:-}"
};
EOF
