# Canonical Terminal-Bench Rust base image from the published canonical list.
FROM public.ecr.aws/docker/library/rust:1.85-slim@sha256:9f841bbe9e7d8e37ceb96ed907265a3a0df7f44e3737d0b100e7907a679acb36

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        nodejs \
        npm \
        python3 \
        python3-pip \
        tmux \
    && python3 -m pip install --break-system-packages --no-cache-dir \
        pytest==8.4.1 \
        pytest-json-ctrf==0.3.5 \
        playwright==1.53.0 \
    && python3 -m playwright install --with-deps chromium \
    && npm install -g esbuild@0.25.8 typescript@5.8.3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app/package.json ./package.json
COPY app/public/ ./public/
COPY app/src/ ./src/
COPY task_file/ ./task_file/

RUN cd /app/task_file \
    && cargo fetch --locked \
    && mkdir -p /app/public/data
