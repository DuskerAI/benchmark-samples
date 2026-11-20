# Canonical Terminal-Bench Debian distro base for custom Zig/CMake toolchains.
FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

ARG ZIG_VERSION=0.13.0
ARG ZIG_SHA256=d45312e61ebcc48032b77bc4cf7fd6915c11fa16e4aad116b66c9468211230ea

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        xz-utils \
        curl \
        cmake \
        ninja-build \
        binutils \
        file \
        python3 \
        python-is-python3 \
        python3-pip \
        tmux \
        asciinema \
    && curl -fsSL "https://ziglang.org/download/${ZIG_VERSION}/zig-linux-x86_64-${ZIG_VERSION}.tar.xz" -o /tmp/zig.tar.xz \
    && printf '%s  /tmp/zig.tar.xz\n' "$ZIG_SHA256" | sha256sum -c - \
    && mkdir -p /opt/zig \
    && tar -xJf /tmp/zig.tar.xz -C /opt/zig --strip-components=1 \
    && ln -s /opt/zig/zig /usr/local/bin/zig \
    && rm /tmp/zig.tar.xz \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages --no-cache-dir \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

COPY task_file/ /app/relkit/

WORKDIR /app/relkit
