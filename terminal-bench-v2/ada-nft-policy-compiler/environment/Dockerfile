FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        gnat \
        gprbuild \
        make \
        tmux \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

COPY nftlab/ /opt/nftlab/
COPY spec.md /opt/nftlab/spec.md
RUN make -C /opt/nftlab install

WORKDIR /opt/nftlab
