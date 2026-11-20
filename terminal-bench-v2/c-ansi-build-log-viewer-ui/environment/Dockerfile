# Canonical C/C++ base for this C repair task; Python is installed only for the pytest/Playwright verifier.
FROM public.ecr.aws/docker/library/gcc:13-bookworm@sha256:930f2ebe239275fa67226654cb79273ea34eee672ae61c8a39f689c37fb7ac5c

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/opt/verifier/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

RUN apt-get update && apt-get install -y --no-install-recommends \
    asciinema \
    ca-certificates \
    libc6-dev \
    make \
    python3 \
    python3-pip \
    python3-venv \
    tmux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/verifier \
    && /opt/verifier/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt \
    && /opt/verifier/bin/playwright install-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && /opt/verifier/bin/playwright install chromium

WORKDIR /app
COPY Makefile /app/Makefile
COPY src/ /app/src/
COPY web/ /app/web/
COPY data/ /app/data/
