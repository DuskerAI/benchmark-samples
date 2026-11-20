FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        coreutils \
        dune \
        libyojson-ocaml-dev \
        make \
        ocaml-findlib \
        ocaml-nox \
        python3 \
        python3-pip \
        python-is-python3 \
        tmux \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages --no-cache-dir \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

COPY .dockerignore /tmp/.dockerignore
COPY README.md /app/README.md
COPY app/dune-project /app/dune-project
COPY app/Makefile /app/Makefile
COPY app/bin/dune /app/bin/dune
COPY app/bin/rekor_gate.ml /app/bin/rekor_gate.ml

WORKDIR /app
