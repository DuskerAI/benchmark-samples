FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        erlang-base \
        erlang-crypto \
        tmux \
        asciinema \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

WORKDIR /app

COPY src/ /app/src/
COPY scripts/ /app/scripts/
COPY samples/ /app/samples/
COPY match_contract.rules /app/match_contract.rules

RUN /app/scripts/build.sh
