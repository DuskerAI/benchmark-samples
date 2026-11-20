# Canonical Debian bookworm-slim base (digest-pinned). Crystal is not in the
# canonical toolchain set, so the pinned Crystal distribution is installed on
# top of this canonical image during build.
FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

ENV DEBIAN_FRONTEND=noninteractive

# Runtime + build dependencies for Crystal and the harness.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tmux \
        asciinema \
        ca-certificates \
        curl \
        gcc \
        pkg-config \
        libpcre2-dev \
        libgc-dev \
        libssl-dev \
        libevent-dev \
        libgmp-dev \
        zlib1g-dev \
        python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Pinned Crystal toolchain (generic linux x86_64 distribution).
ARG CRYSTAL_VERSION=1.14.0
RUN curl -fsSL -o /tmp/crystal.tar.gz \
        "https://github.com/crystal-lang/crystal/releases/download/${CRYSTAL_VERSION}/crystal-${CRYSTAL_VERSION}-1-linux-x86_64.tar.gz" \
    && mkdir -p /opt/crystal \
    && tar -xzf /tmp/crystal.tar.gz -C /opt/crystal --strip-components=1 \
    && rm /tmp/crystal.tar.gz \
    && ln -s /opt/crystal/bin/crystal /usr/local/bin/crystal \
    && ln -s /opt/crystal/bin/shards /usr/local/bin/shards \
    && crystal --version

# Verifier tooling.
RUN ln -s /usr/bin/python3 /usr/local/bin/python \
    && python3 -m pip install --no-cache-dir --break-system-packages \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

# Ship the linker sources into the image.
COPY task_file/ /app/
RUN chmod +x /app/scripts/build.sh

WORKDIR /app
