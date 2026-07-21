/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

// Injected at container startup by docker/generate-env-config.sh (see
// index.html's <script src="/env-config.js">), not at Vite build time -
// see Dockerfile's note. Absent entirely in local `npm run dev` (no
// entrypoint script has run), so every read of this must fall back to
// import.meta.env, same as before this existed.
interface Window {
  __RUNTIME_CONFIG__?: {
    VITE_API_BASE_URL?: string
  }
}
