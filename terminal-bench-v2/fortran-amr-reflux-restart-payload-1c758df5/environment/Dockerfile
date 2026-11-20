FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gfortran \
        make \
        tmux \
        asciinema \
    && rm -rf /var/lib/apt/lists/*

COPY wheels/ /tmp/wheels/
RUN python -m pip install --no-index --find-links=/tmp/wheels --no-cache-dir \
        iniconfig==2.3.0 \
        packaging==26.2 \
        pluggy==1.6.0 \
        pygments==2.20.0 \
        pytest==8.4.1 \
        pytest-json-ctrf==0.3.5 \
    && rm -rf /tmp/wheels

WORKDIR /app/amr
COPY Makefile /app/amr/Makefile
COPY src/ /app/amr/src/
RUN make
