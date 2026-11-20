# Canonical TB2 Debian base; Ada/GNAT has no language-specific canonical image.
FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/opt/pytest-venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt /tmp/requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        coreutils \
        gnat-12 \
        libssl-dev \
        make \
        python-is-python3 \
        python3 \
        python3-pip \
        python3-venv \
        tmux \
    && python3 -m venv /opt/pytest-venv \
    && /opt/pytest-venv/bin/python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && groupadd --system agent \
    && useradd --system --gid agent --create-home --home-dir /home/agent --shell /bin/bash agent \
    && mkdir -p /logs/verifier \
    && chown root:root /logs /logs/verifier \
    && chmod 0755 /logs \
    && chmod 0700 /logs/verifier \
    && rm -rf /var/lib/apt/lists/* /tmp/requirements.txt

COPY pcrgate/ /app/pcrgate/

WORKDIR /app

RUN mkdir -p /app/pcrgate/bin \
    && chown -R agent:agent /app/pcrgate \
    && chmod -R u+rwX,go+rX /app/pcrgate
