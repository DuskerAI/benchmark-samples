FROM public.ecr.aws/docker/library/gcc:13-bookworm@sha256:930f2ebe239275fa67226654cb79273ea34eee672ae61c8a39f689c37fb7ac5c

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        python3 \
        python3-pip \
        python-is-python3 \
        tmux \
    && python3 -m pip install --break-system-packages --no-cache-dir \
        pytest==8.4.1 \
        pytest-json-ctrf==0.3.5 \
    && rm -rf /var/lib/apt/lists/*

COPY Makefile /opt/dax-policy/Makefile
COPY include/ /opt/dax-policy/include/
COPY src/ /opt/dax-policy/src/
WORKDIR /opt/dax-policy
RUN make \
    && mkdir -p /usr/local/libexec /etc/dax-rollout /var/lib/dax-rollout/result \
    && cp build/daxadmin /usr/local/libexec/daxadmin

COPY incident.plan /var/lib/dax-rollout/incident.plan
COPY change.plan /etc/dax-rollout/change.plan
COPY --chmod=0755 dax-rollout /usr/local/sbin/dax-rollout
WORKDIR /var/lib/dax-rollout
