# Erlang is installed from the canonical Debian base because Terminal-Bench
# does not yet provide a canonical Erlang image.
FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        erlang-base \
        erlang-dev \
        make \
        python3 \
        python-is-python3 \
        python3-venv \
        tmux \
        asciinema \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /opt/quic_gate

COPY Makefile /opt/quic_gate/Makefile
COPY bin/ /opt/quic_gate/bin/
COPY src/ /opt/quic_gate/src/

RUN chmod +x /opt/quic_gate/bin/quic_gate \
    && make -C /opt/quic_gate
