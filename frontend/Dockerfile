# Build stage: compile the dashboard.
FROM docker.io/library/node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# Runtime: unprivileged nginx (runs as non-root, listens on 8080).
FROM docker.io/nginxinc/nginx-unprivileged:stable-alpine
ENV LAB_LISTEN_PORT=8080 \
    LAB_STATIC_ROOT=/usr/share/nginx/html \
    LAB_API_UPSTREAM=http://api:8000 \
    NGINX_ENVSUBST_FILTER=^LAB_
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
