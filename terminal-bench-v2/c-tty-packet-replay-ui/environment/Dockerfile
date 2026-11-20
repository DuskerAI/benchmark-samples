# Canonical Terminal-Bench Python base, digest-pinned per dockerfile-best-practices.md.
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/opt/verifier/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

RUN apt-get update && apt-get install -y --no-install-recommends \
    asciinema=2.2.0-1 \
    ca-certificates=20230311+deb12u1 \
    gcc=4:12.2.0-3 \
    libc6-dev=2.36-9+deb12u14 \
    make=4.3-4.1 \
    tmux=3.3a-3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/verifier \
    && /opt/verifier/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt \
    && /opt/verifier/bin/playwright install-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && /opt/verifier/bin/playwright install chromium

WORKDIR /app
COPY app/ /app/
