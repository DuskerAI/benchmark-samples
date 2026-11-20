# Canonical Terminal-Bench Rust base image, digest-pinned for reproducibility.
FROM public.ecr.aws/docker/library/rust:1.85-slim@sha256:9f841bbe9e7d8e37ceb96ed907265a3a0df7f44e3737d0b100e7907a679acb36

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/usr/local/cargo/bin:${PATH}"
RUN ln -sf /usr/local/cargo/bin/cargo /usr/local/bin/cargo \
    && ln -sf /usr/local/cargo/bin/rustc /usr/local/bin/rustc

RUN apt-get update && apt-get install -y --no-install-recommends \
    asciinema \
    ca-certificates \
    curl \
    git \
    procps \
    python3 \
    python3-pip \
    tmux \
    && rm -rf /var/lib/apt/lists/*

COPY wheels/ /tmp/wheels/
RUN python3 -m pip install --break-system-packages --no-index --find-links=/tmp/wheels \
    pytest==8.3.4 \
    pytest-json-ctrf==0.3.5 \
    && rm -rf /tmp/wheels

WORKDIR /app
COPY task_file/ /app/
RUN cargo build --release
