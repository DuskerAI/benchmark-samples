# Canonical Terminal-Bench Rust base image from dockerfile-best-practices.md.
FROM public.ecr.aws/docker/library/rust:1.85-slim@sha256:9f841bbe9e7d8e37ceb96ed907265a3a0df7f44e3737d0b100e7907a679acb36

ENV DEBIAN_FRONTEND=noninteractive \
    CARGO_NET_OFFLINE=true \
    PATH=/usr/local/cargo/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        gcc \
        libc6-dev \
        python-is-python3 \
        python3 \
        python3-pip \
        tmux \
    && pip3 install --break-system-packages --no-cache-dir pytest==8.4.1 pytest-json-ctrf==0.3.5 \
    && rm -rf /var/lib/apt/lists/*

COPY qrun/Cargo.toml qrun/Cargo.lock /app/qrun/
COPY qrun/spec.md /app/qrun/spec.md
COPY qrun/src/ /app/qrun/src/
COPY qrun/artifacts/ /app/qrun/artifacts/

WORKDIR /app/qrun
RUN cargo build --release --locked
