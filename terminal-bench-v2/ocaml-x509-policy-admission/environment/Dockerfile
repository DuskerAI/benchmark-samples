FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

USER root
ENV DEBIAN_FRONTEND=noninteractive

COPY wheels/ /tmp/wheels/
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        make \
        ocaml-nox \
        tmux \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-index --find-links=/tmp/wheels --no-cache-dir \
        pytest==8.4.1 \
        pytest-json-ctrf==0.3.5 \
    && rm -rf /tmp/wheels

WORKDIR /app
COPY Makefile /app/Makefile
COPY spec.md /app/spec.md
COPY src/ /app/src/
RUN make -C /app clean all
