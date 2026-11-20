# Canonical TB2 Ruby base image from the current approved list.
FROM public.ecr.aws/docker/library/ruby:3.3-slim-bookworm@sha256:e76733e94b3a5893e4a141024ef3a583dc10781dc24becebf74f9c9f9a33e3df

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        python-is-python3 \
        python3 \
        python3-pip \
        tmux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /tmp/requirements.lock
RUN python3 -m pip install --break-system-packages --no-cache-dir --require-hashes \
        -r /tmp/requirements.lock \
    && rm /tmp/requirements.lock

COPY auditor/ /opt/measured_boot_auditor/
RUN chmod +x /opt/measured_boot_auditor/bin/boot_audit

WORKDIR /opt/measured_boot_auditor
