# Canonical Debian is used because the canonical image set has no Elixir runtime.
FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

ENV LANG=C.UTF-8

RUN apt-get update \
    && apt-get install -y --no-install-recommends asciinema ca-certificates elixir erlang-base erlang-crypto python3 python3-pip python-is-python3 tmux \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages --no-cache-dir pytest==8.4.1 pytest-json-ctrf==0.3.5

COPY arbiter.exs /app/arbiter.exs
RUN chmod +x /app/arbiter.exs
WORKDIR /app
