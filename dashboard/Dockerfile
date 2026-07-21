FROM node:22-alpine AS build
WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# Runtime config (roadmap item D2): the API URL and shared secret are no
# longer baked in at build time. The image serves a runtime-config.js
# generated at container START from the API_BASE_URL / API_SHARED_SECRET
# environment variables (docker/40-runtime-config.sh below) - point one
# built image at any backend with `docker run -e`, no rebuild.
#
# The VITE_* build args remain only as a legacy fallback for anyone still
# building with them; runtime config wins when both are present
# (src/lib/api.ts).
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ARG VITE_API_SHARED_SECRET=
ENV VITE_API_SHARED_SECRET=$VITE_API_SHARED_SECRET
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# nginx:alpine runs /docker-entrypoint.d/*.sh before serving - this is
# what regenerates runtime-config.js from env vars on every start.
COPY docker/40-runtime-config.sh /docker-entrypoint.d/40-runtime-config.sh
RUN chmod +x /docker-entrypoint.d/40-runtime-config.sh
EXPOSE 80
