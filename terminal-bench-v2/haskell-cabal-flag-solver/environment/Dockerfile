# Canonical TB2 Debian base; no canonical Haskell runtime image is currently listed.
FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/opt/pytest-venv/bin:${PATH}"

# Pin the validated Debian bookworm GHC and cabal-install packages explicitly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        cabal-install=3.4.1.0-3 \
        ca-certificates \
        ghc=9.0.2-4 \
        python3 \
        python3-pip \
        python3-venv \
        tmux \
    && python3 -m venv /opt/pytest-venv \
    && /opt/pytest-venv/bin/python -m pip install --no-cache-dir \
        pytest==8.4.1 \
        pytest-json-ctrf==0.3.5 \
    && mkdir -p /root/.cabal \
    && touch /root/.cabal/config \
    && rm -rf /var/lib/apt/lists/*

COPY cabal-workspace/ /app/cabal-workspace/
