# Canonical Terminal-Bench Ubuntu 24.04 distro base for uncommon toolchains such as Crystal.
FROM public.ecr.aws/docker/library/ubuntu:24.04@sha256:0d39fcc8335d6d74d5502f6df2d30119ff4790ebbb60b364818d5112d9e3e932

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        crystal \
        make \
        python3 \
        python-is-python3 \
        python3-pip \
        tmux \
    && python3 -m pip install --break-system-packages --no-cache-dir \
        pytest==8.4.1 \
        pytest-json-ctrf==0.3.5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY src/ /app/src/
COPY model/ /app/model/
RUN mkdir -p /app/spec
COPY spec.md /app/spec/spec.md
RUN mkdir -p /app/build \
    && printf '%s\n' \
        '#!/bin/sh' \
        'set -eu' \
        'mkdir -p /app/build' \
        'if [ ! -x /app/build/qinfer ] || [ /app/src/qinfer.cr -nt /app/build/qinfer ]; then' \
        '  if ! crystal build /app/src/qinfer.cr -o /app/build/qinfer >/tmp/qinfer-build.log 2>&1; then' \
        '    cat /tmp/qinfer-build.log >&2' \
        '    exit 1' \
        '  fi' \
        'fi' \
        'exec /app/build/qinfer "$@"' \
        > /usr/local/bin/qinfer \
    && chmod +x /usr/local/bin/qinfer
