FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        acl \
        asciinema \
        ca-certificates \
        logrotate \
        openssh-server \
        python-is-python3 \
        python3 \
        python3-pip \
        python3-venv \
        sudo \
        tmux \
    && pip3 install --break-system-packages --no-cache-dir pytest==8.4.1 pytest-json-ctrf==0.3.5 \
    && python3 -m venv --system-site-packages /opt/verifier-venv \
    && mkdir -p /run/sshd \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/lyra
COPY README.md /opt/lyra/README.md

WORKDIR /workspace
