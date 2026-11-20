# There is no canonical Nim image in the TB2 base list. Use the canonical
# Python image so the verifier's pinned pytest runner is available, then install
# Nim from Debian for the agent-facing source build.
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        coreutils \
        gcc \
        libc6-dev \
        make \
        nim=1.6.10-2 \
        openssl \
        tmux \
        asciinema \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

COPY x509audit /opt/x509audit

RUN make -C /opt/x509audit

WORKDIR /app
