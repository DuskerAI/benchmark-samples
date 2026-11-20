FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        gfortran \
        make \
        strace \
        tmux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt

RUN python -m pip install --no-cache-dir \
    -r /tmp/requirements.txt

RUN mkdir -p /opt/swe

WORKDIR /app

COPY Makefile /opt/swe/
COPY README.md /opt/swe/README.md
COPY src/ /opt/swe/src/
