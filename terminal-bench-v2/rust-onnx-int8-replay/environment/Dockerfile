FROM public.ecr.aws/docker/library/rust:1.85-slim@sha256:9f841bbe9e7d8e37ceb96ed907265a3a0df7f44e3737d0b100e7907a679acb36

COPY requirements.lock /tmp/requirements.lock

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema=2.2.0-1 \
        python3=3.11.2-1+b1 \
        python3-pip=23.0.1+dfsg-1 \
        python-is-python3=3.11.2-1+deb12u1 \
        tmux=3.3a-3 \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages --no-cache-dir \
        --require-hashes --no-deps -r /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock

COPY qcert/Cargo.toml /app/Cargo.toml
COPY qcert/Cargo.lock /app/Cargo.lock
COPY qcert/src/ /app/src/
COPY cases/ /app/cases/
COPY spec.md /app/spec.md

WORKDIR /app
