# Zig has no canonical language-specific TB2 base yet; use canonical digest-pinned Ubuntu and verify the Zig toolchain download.
FROM public.ecr.aws/docker/library/ubuntu:24.04@sha256:0d39fcc8335d6d74d5502f6df2d30119ff4790ebbb60b364818d5112d9e3e932

ENV DEBIAN_FRONTEND=noninteractive \
    ZIG_VERSION=0.13.0 \
    ZIG_SHA256=d45312e61ebcc48032b77bc4cf7fd6915c11fa16e4aad116b66c9468211230ea

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema=2.4.0-1 \
        ca-certificates=20240203 \
        curl=8.5.0-2ubuntu10.11 \
        python3=3.12.3-0ubuntu2.1 \
        python3-pip=24.0+dfsg-1ubuntu1.3 \
        tmux=3.4-1ubuntu0.1 \
        xz-utils=5.6.1+really5.4.5-1ubuntu0.3 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSLo /tmp/zig.tar.xz "https://ziglang.org/download/${ZIG_VERSION}/zig-linux-x86_64-${ZIG_VERSION}.tar.xz" \
    && printf '%s  /tmp/zig.tar.xz\n' "$ZIG_SHA256" | sha256sum -c - \
    && mkdir -p /opt/zig \
    && tar -xJf /tmp/zig.tar.xz -C /opt/zig --strip-components=1 \
    && ln -s /opt/zig/zig /usr/local/bin/zig \
    && rm /tmp/zig.tar.xz

RUN ln -s /usr/bin/python3 /usr/local/bin/python

COPY requirements-verifier.txt /tmp/requirements-verifier.txt
RUN python3 -m pip install --break-system-packages --no-cache-dir -r /tmp/requirements-verifier.txt \
    && rm /tmp/requirements-verifier.txt

COPY ebpf-admit/ /opt/ebpf-admit/
RUN chmod +x /opt/ebpf-admit/scripts/build.sh /opt/ebpf-admit/scripts/check.sh \
    && /opt/ebpf-admit/scripts/build.sh

WORKDIR /opt/ebpf-admit
