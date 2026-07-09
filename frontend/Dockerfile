# ---------- build stage ----------
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# ---------- runtime stage ----------
FROM nginx:alpine

# Runtime-config template — nginx envsubst renders /config.js and proxies /api.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

# Defaults; overridden by Container App env.
ENV BACKEND_URL="http://localhost:8000" \
    AUTH_MODE="mock" \
    ENTRA_TENANT_ID="" \
    ENTRA_CLIENT_ID="" \
    ENTRA_API_SCOPE="" \
    BUILD_ID="dev"

EXPOSE 8080
