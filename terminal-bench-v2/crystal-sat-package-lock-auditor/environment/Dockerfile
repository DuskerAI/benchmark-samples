# No canonical t-bench Crystal base image exists; Ubuntu 24.04 pins Crystal 1.11.2 through apt.
FROM public.ecr.aws/docker/library/ubuntu:24.04@sha256:0d39fcc8335d6d74d5502f6df2d30119ff4790ebbb60b364818d5112d9e3e932

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        crystal=1.11.2+dfsg-1build3 \
        python3 \
        python3-pip \
        tmux \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-verifier.txt /tmp/requirements-verifier.txt
RUN python -m pip install --break-system-packages --no-cache-dir --require-hashes \
    -r /tmp/requirements-verifier.txt

COPY plock/ /app/plock/
RUN chmod +x /app/plock/bin/build.sh

WORKDIR /app
