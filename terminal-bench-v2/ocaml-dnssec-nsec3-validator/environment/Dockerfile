# Canonical TB2 Debian base; OCaml is installed from Debian packages because the canonical list has no OCaml runtime image.
FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/opt/pytest-venv/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        coreutils \
        make \
        ocaml \
        python3 \
        python3-pip \
        python3-venv \
        tmux \
    && python3 -m venv /opt/pytest-venv \
    && /opt/pytest-venv/bin/python -m pip install --no-cache-dir \
        pytest==8.4.1 \
        pytest-json-ctrf==0.3.5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY Makefile /app/Makefile
COPY spec.md /app/spec.md
COPY src/ /app/src/
RUN make -C /app clean all
