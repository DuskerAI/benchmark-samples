# No canonical Julia image exists in the Terminal-Bench base list, so this task
# uses digest-pinned debian-slim with manual Julia and harness-tool installation.
FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

ARG JULIA_VERSION=1.11.6
ARG JULIA_SHA256_X86_64=e99e52e2029d845097c68f2372d836186f0eb3fb897a9dde0bdf9ee9250d03d5
ARG JULIA_SHA256_AARCH64=c2c5cdce017cacadaccb7d22aa070f549e4e87c4bb10f15853170ddcb50bf5f4
ARG TARGETARCH

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema=2.2.0-1 \
        ca-certificates=20230311+deb12u1 \
        curl=7.88.1-10+deb12u15 \
        python-is-python3=3.11.2-1+deb12u1 \
        python3=3.11.2-1+b1 \
        python3-pip=23.0.1+dfsg-1 \
        tmux=3.3a-3 \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
        amd64) julia_dir="x64"; julia_platform="x86_64"; julia_sha256="${JULIA_SHA256_X86_64}" ;; \
        arm64) julia_dir="aarch64"; julia_platform="aarch64"; julia_sha256="${JULIA_SHA256_AARCH64}" ;; \
        *) echo "unsupported architecture: ${TARGETARCH:-$(dpkg --print-architecture)}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://julialang-s3.julialang.org/bin/linux/${julia_dir}/1.11/julia-${JULIA_VERSION}-linux-${julia_platform}.tar.gz" -o /tmp/julia.tar.gz \
    && echo "${julia_sha256}  /tmp/julia.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/julia \
    && tar -xzf /tmp/julia.tar.gz -C /opt/julia --strip-components=1 \
    && ln -s /opt/julia/bin/julia /usr/local/bin/julia \
    && rm /tmp/julia.tar.gz

RUN python -m pip install --no-cache-dir --break-system-packages \
    numpy==2.3.1 \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

COPY task_file /app/task_file

RUN julia --project=/app/task_file -e 'using Pkg; Pkg.instantiate(); using LinearAlgebra, SparseArrays, TOML, SHA, Printf'
