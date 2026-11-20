# Canonical Terminal-Bench Rust base image. The task needs the Rust toolchain
# available during both the agent run and verifier-time cargo rebuilds.
FROM public.ecr.aws/docker/library/rust:1.85-slim@sha256:9f841bbe9e7d8e37ceb96ed907265a3a0df7f44e3737d0b100e7907a679acb36

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        python3 \
        python3-pip \
        tmux \
    && python3 -m pip install --break-system-packages --no-cache-dir pytest==8.4.1 pytest-json-ctrf==0.3.5 \
    && rm -rf /var/lib/apt/lists/*

COPY task_file/Cargo.toml task_file/Cargo.lock ./task_file/
COPY task_file/src/ ./task_file/src/

RUN cd /app/task_file && cargo build --locked
