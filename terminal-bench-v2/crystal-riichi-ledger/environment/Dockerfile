# Crystal does not yet have a canonical Terminal-Bench base image; use a digest-pinned Ubuntu base
# with a SHA256-verified upstream Crystal archive instead of an unpinned language image.
FROM public.ecr.aws/docker/library/ubuntu:24.04@sha256:0d39fcc8335d6d74d5502f6df2d30119ff4790ebbb60b364818d5112d9e3e932

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC

ARG CRYSTAL_VERSION=1.11.2
ARG CRYSTAL_SHA256=60c16801156af9a44cee25d408738cce3697c4000e0624a2ffb017a9bba4d91d

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gcc \
        libc6-dev \
        make \
        python3 \
        python3-pip \
        tmux \
        asciinema \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://github.com/crystal-lang/crystal/releases/download/${CRYSTAL_VERSION}/crystal-${CRYSTAL_VERSION}-1-linux-x86_64-bundled.tar.gz" -o /tmp/crystal.tar.gz \
    && echo "${CRYSTAL_SHA256}  /tmp/crystal.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/crystal.tar.gz -C /opt \
    && ln -s "/opt/crystal-${CRYSTAL_VERSION}-1/bin/crystal" /usr/local/bin/crystal \
    && ln -s "/opt/crystal-${CRYSTAL_VERSION}-1/bin/shards" /usr/local/bin/shards \
    && rm /tmp/crystal.tar.gz

RUN python3 -m pip install --break-system-packages --no-cache-dir \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

COPY app /app
WORKDIR /app
