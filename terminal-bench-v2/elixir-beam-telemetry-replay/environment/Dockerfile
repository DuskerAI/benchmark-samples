# Canonical Terminal-Bench Debian base; Elixir/Erlang are installed from the distro packages because the canonical list has no Elixir runtime image.
FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

ENV MIX_ENV=prod \
    LANG=C.UTF-8

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        elixir \
        erlang-base \
        erlang-crypto \
        erlang-syntax-tools \
        python3 \
        python3-pip \
        tmux \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages --no-cache-dir \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

COPY task_file/ /app/beamtrail/

WORKDIR /app/beamtrail
