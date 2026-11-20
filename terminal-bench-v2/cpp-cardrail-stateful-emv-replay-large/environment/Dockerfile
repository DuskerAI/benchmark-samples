FROM public.ecr.aws/docker/library/ubuntu:24.04@sha256:0d39fcc8335d6d74d5502f6df2d30119ff4790ebbb60b364818d5112d9e3e932

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    asciinema \
    ca-certificates \
    curl \
    g++ \
    git \
    make \
    procps \
    python3 \
    python3-pip \
    tmux \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages --no-cache-dir \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

WORKDIR /app
COPY task_file/cardrail /app/cardrail
COPY task_file/etc/cardrail /etc/cardrail
RUN mkdir -p /app/cardrail/bin /app/cardrail/output /app/cardrail/state
