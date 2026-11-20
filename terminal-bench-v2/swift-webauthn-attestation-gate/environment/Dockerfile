# Canonical Terminal-Bench Ubuntu distro base; Swift is installed as a pinned
# upstream toolchain because the canonical image list has no Swift runtime row.
FROM public.ecr.aws/docker/library/ubuntu:24.04@sha256:0d39fcc8335d6d74d5502f6df2d30119ff4790ebbb60b364818d5112d9e3e932

ARG SWIFT_VERSION=6.1.2
ARG SWIFT_SHA256=d749d5fe2d6709ee988e96b16f02bca7b53304d09925e31063fd5ec56019de9f
ARG SWIFT_URL=https://download.swift.org/swift-6.1.2-release/ubuntu2404/swift-6.1.2-RELEASE/swift-6.1.2-RELEASE-ubuntu24.04.tar.gz

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/opt/venv/bin:/opt/swift/usr/bin:${PATH}"
ENV LD_LIBRARY_PATH="/opt/swift/usr/lib/swift/linux"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        binutils \
        ca-certificates \
        clang \
        curl \
        git \
        libc6-dev \
        libcurl4-openssl-dev \
        libedit2 \
        libgcc-13-dev \
        libicu-dev \
        libncurses6 \
        libsqlite3-0 \
        libstdc++-13-dev \
        libxml2-dev \
        openssl \
        python3 \
        python-is-python3 \
        python3-pip \
        python3-venv \
        tmux \
        tzdata \
        xz-utils \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "${SWIFT_URL}" -o /tmp/swift.tar.gz \
    && echo "${SWIFT_SHA256}  /tmp/swift.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/swift \
    && tar -xzf /tmp/swift.tar.gz -C /opt/swift --strip-components=1 \
    && rm /tmp/swift.tar.gz \
    && swift --version | grep -F "Swift version ${SWIFT_VERSION}"

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir \
        pytest==8.4.1 \
        pytest-json-ctrf==0.3.5

COPY webauthngate/ /app/webauthngate/
RUN chmod +x /app/webauthngate/scripts/build.sh

WORKDIR /app/webauthngate
