# Canonical Terminal-Bench Rust base image.
FROM public.ecr.aws/docker/library/rust:1.85-slim@sha256:9f841bbe9e7d8e37ceb96ed907265a3a0df7f44e3737d0b100e7907a679acb36

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    procps \
    git \
    tmux \
    asciinema \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir --break-system-packages \
    iniconfig==2.3.0 \
    packaging==26.2 \
    pluggy==1.6.0 \
    pygments==2.20.0 \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

RUN printf 'export PATH="/usr/local/cargo/bin:$PATH"\n' > /etc/profile.d/cargo.sh \
    && printf 'export PATH="/usr/local/cargo/bin:$PATH"\n' >> /etc/bash.bashrc

WORKDIR /app

COPY task_file/ /app/
