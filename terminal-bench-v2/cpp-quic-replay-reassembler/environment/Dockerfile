FROM public.ecr.aws/docker/library/gcc:13-bookworm@sha256:930f2ebe239275fa67226654cb79273ea34eee672ae61c8a39f689c37fb7ac5c
# Digest-pinned GCC image is used because this C++ repair task needs a C++17 compiler; no sanctioned t-bench C++ compiler base is available in the local workflow cache.

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tmux \
        asciinema \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --break-system-packages --no-cache-dir -r /tmp/requirements.txt

COPY quic-replay/ /opt/quic-replay/
COPY runtime.conf /etc/quic-replay/runtime.conf

RUN python3 /opt/quic-replay/tools/materialize_fixtures.py \
    && chmod +x /opt/quic-replay/scripts/build.sh

WORKDIR /opt/quic-replay
