FROM public.ecr.aws/docker/library/gcc:13-bookworm@sha256:930f2ebe239275fa67226654cb79273ea34eee672ae61c8a39f689c37fb7ac5c

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    asciinema \
    ca-certificates \
    curl \
    git \
    make \
    procps \
    python3 \
    python3-pip \
    python3-pkg-resources \
    python3-setuptools \
    python3-wheel \
    tmux \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages --no-cache-dir \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

RUN ln -s /usr/bin/python3 /usr/local/bin/python

WORKDIR /app

COPY task_file/flowguard/ /opt/flowguard/
COPY task_file/etc/flowguard/ /etc/flowguard/
COPY task_file/var/lib/flowguard/ /var/lib/flowguard/
RUN mkdir -p /opt/flowguard/bin /var/lib/flowguard/output
