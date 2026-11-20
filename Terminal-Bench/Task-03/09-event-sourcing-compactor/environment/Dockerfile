FROM node:20.11.0-bookworm-slim AS builder

WORKDIR /build
COPY task_file/package.json task_file/package-lock.json* ./
RUN npm ci --silent
COPY task_file/ ./
RUN npm run build

# ---- runtime ----
# Use postgres base image for easy cluster init, then layer Node on top.
FROM postgres:16-bookworm

# Install Node.js 20
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy Node module cache so the agent can rebuild offline
COPY --from=builder /build/node_modules /app/node_modules

# Pre-built JS for the initial run
COPY --from=builder /build/dist /app/dist

# Full source so the agent can edit and rebuild
COPY task_file/ /app/

ENV POSTGRES_DB=orders_db \
    POSTGRES_PASSWORD=secret \
    POSTGRES_USER=postgres \
    POSTGRES_HOST=localhost \
    POSTGRES_PORT=5432 \
    PORT=3000

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
