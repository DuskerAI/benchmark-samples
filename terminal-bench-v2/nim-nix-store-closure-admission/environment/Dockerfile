# Nim is not covered by the canonical Terminal-Bench base image list; this
# digest-pinned Debian image provides the apt-packaged Nim compiler.
FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONNOUSERSITE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        gcc \
        libc6-dev \
        make \
        nim \
        python3 \
        python-is-python3 \
        python3-pip \
        python3-venv \
        tmux \
    && python3 -m venv /opt/storegate-verifier \
    && /opt/storegate-verifier/bin/python -m pip install --no-cache-dir pytest==8.4.1 pytest-json-ctrf==0.3.5 \
    && groupadd --system agent \
    && useradd --system --gid agent --create-home --home-dir /home/agent --shell /bin/bash agent \
    && mkdir -p /logs/verifier \
    && chown -R root:root /opt/storegate-verifier /logs \
    && chmod -R go-w /opt/storegate-verifier \
    && chmod 755 /logs /logs/verifier \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/storegate

COPY Makefile /app/storegate/Makefile
COPY spec.md /app/storegate/spec.md
COPY scripts/ /app/storegate/scripts/
COPY src/ /app/storegate/src/
COPY cases/ /app/storegate/cases/

RUN mkdir -p /app/storegate/bin \
    && chown -R agent:agent /app/storegate \
    && chmod -R u+rwX,go+rX /app/storegate
