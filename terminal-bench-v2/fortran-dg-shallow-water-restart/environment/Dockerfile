FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gfortran-12 \
        make \
        tmux \
        asciinema \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
COPY wheels/ /tmp/wheels/
RUN python -m pip install --no-index --find-links=/tmp/wheels --no-cache-dir --require-hashes \
    -r /tmp/requirements.txt \
    && rm -rf /tmp/wheels

WORKDIR /app
COPY dg_swe/ /app/dg_swe/
WORKDIR /app/dg_swe
RUN make clean && make
WORKDIR /app
