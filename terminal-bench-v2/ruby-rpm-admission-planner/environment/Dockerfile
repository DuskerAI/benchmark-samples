# Canonical Terminal-Bench Ruby runtime from .claude/docs/tb2/dockerfile-best-practices.md.
FROM public.ecr.aws/docker/library/ruby:3.3-slim-bookworm@sha256:e76733e94b3a5893e4a141024ef3a583dc10781dc24becebf74f9c9f9a33e3df

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 \
        python-is-python3 \
        python3-pip \
        tmux \
        asciinema \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages --no-cache-dir \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

COPY rpmgate/ /app/rpmgate/

RUN chmod +x /app/rpmgate/bin/build.sh /app/rpmgate/bin/rpm-admit

WORKDIR /app
