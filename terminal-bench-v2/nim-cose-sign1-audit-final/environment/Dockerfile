# Canonical Terminal-Bench Debian base; Nim is installed from distro packages because the canonical list has no Nim-specific runtime image.
FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        gcc \
        libc6-dev \
        make \
        nim=1.6.10-2 \
        openssl \
        python3 \
        python-is-python3 \
        python3-pip \
        tmux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /tmp/requirements.lock
RUN python3 -m pip install --break-system-packages --no-cache-dir --require-hashes --no-deps \
        -r /tmp/requirements.lock \
    && rm /tmp/requirements.lock

WORKDIR /app/coseaudit

COPY Makefile /app/coseaudit/Makefile
COPY src/ /app/coseaudit/src/
COPY fixtures/ /app/coseaudit/fixtures/
