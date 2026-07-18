FROM node:22-alpine AS build
WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# Vite bakes VITE_* env vars in at build time, not runtime - the browser
# calls this URL directly (it's not proxied through the dashboard
# container), so it needs to be whatever the API is reachable at from the
# user's browser, not from inside the Docker network.
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
# Must match the backend's API_SHARED_SECRET, if set - see the note in
# dashboard/src/lib/api.ts. Baked in at build time same as the URL above.
ARG VITE_API_SHARED_SECRET=
ENV VITE_API_SHARED_SECRET=$VITE_API_SHARED_SECRET
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
