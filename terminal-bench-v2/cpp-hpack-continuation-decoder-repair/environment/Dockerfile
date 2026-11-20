# Canonical Terminal-Bench C/C++ GCC base; digest pinned for reproducibility.
FROM public.ecr.aws/docker/library/gcc:13-bookworm@sha256:930f2ebe239275fa67226654cb79273ea34eee672ae61c8a39f689c37fb7ac5c

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        make \
        python3 \
        python3-pip \
        tmux \
        asciinema \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir --break-system-packages \
        pytest==8.4.1 \
        pytest-json-ctrf==0.3.5

COPY hpackoscope /opt/hpackoscope
COPY runtime.conf /etc/hpackoscope/runtime.conf

RUN mkdir -p /var/lib/hpackoscope/output \
    && chmod +x /opt/hpackoscope/scripts/build.sh \
    && /opt/hpackoscope/scripts/build.sh

WORKDIR /opt/hpackoscope
