FROM public.ecr.aws/docker/library/rust:1.85-slim@sha256:9f841bbe9e7d8e37ceb96ed907265a3a0df7f44e3737d0b100e7907a679acb36

COPY requirements-verifier.txt /tmp/requirements-verifier.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        python3 \
        python3-pip \
        python-is-python3 \
        tmux \
    && pip3 install --break-system-packages --no-cache-dir --require-hashes \
        -r /tmp/requirements-verifier.txt \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY Cargo.toml /app/Cargo.toml
COPY Cargo.lock /app/Cargo.lock
COPY spec.md /app/spec.md
COPY src /app/src
RUN cargo build --release --locked --offline
COPY --chmod=0755 store-evidence-attest /usr/local/sbin/store-evidence-attest
