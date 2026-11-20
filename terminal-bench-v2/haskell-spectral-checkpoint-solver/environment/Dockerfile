# Terminal-Bench's canonical language-runtime list has no Haskell/GHC image yet;
# this task uses the canonical digest-pinned Debian base and installs GHC there.
FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/opt/pytest-venv/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        ghc \
        make \
        python3 \
        python3-pip \
        python3-venv \
        tmux \
    && python3 -m venv /opt/pytest-venv \
    && /opt/pytest-venv/bin/python -m pip install --no-cache-dir \
        pytest==8.4.1 \
        pytest-json-ctrf==0.3.5 \
    && rm -rf /var/lib/apt/lists/*

COPY spectral/ /app/spectral/
