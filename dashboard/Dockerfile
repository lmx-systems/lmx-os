FROM node:22-alpine AS build
WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# No VITE_API_BASE_URL baked in here anymore (docs/ROADMAP.md D2) - the
# browser reads window.__RUNTIME_CONFIG__ instead (src/lib/api.ts),
# written by docker/generate-env-config.sh at *container* startup from a
# real env var, not at image build time.
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Picked up automatically by the base image's own entrypoint before nginx
# starts - see docker/generate-env-config.sh's docstring.
COPY docker/generate-env-config.sh /docker-entrypoint.d/40-generate-env-config.sh
RUN chmod +x /docker-entrypoint.d/40-generate-env-config.sh
EXPOSE 80
