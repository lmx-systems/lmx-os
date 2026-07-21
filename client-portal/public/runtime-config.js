// Runtime configuration (roadmap item D2). In local dev this file is
// served as-is from public/ and sets nothing - src/lib/api.ts falls back
// to VITE_* env vars / localhost defaults. In the Docker image it is
// REGENERATED at container start from environment variables (see
// docker/40-runtime-config.sh), which is what lets one built image point
// at any API URL via `docker run -e API_BASE_URL=...` instead of a rebuild.
window.__LMX_RUNTIME_CONFIG__ = {}
