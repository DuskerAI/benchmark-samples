# Canonical Terminal-Bench C/C++ GCC base image, pinned by digest.
FROM public.ecr.aws/docker/library/gcc:13-bookworm@sha256:930f2ebe239275fa67226654cb79273ea34eee672ae61c8a39f689c37fb7ac5c

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    asciinema=2.2.0-1 \
    binutils=2.40-2 \
    ca-certificates=20230311+deb12u1 \
    cmake=3.25.1-1 \
    curl=7.88.1-10+deb12u14 \
    git=1:2.39.5-0+deb12u3 \
    make=4.3-4.1 \
    procps=2:4.0.2-3 \
    python3=3.11.2-1+b1 \
    python3-pip=23.0.1+dfsg-1 \
    tmux=3.3a-3 \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir --break-system-packages \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

WORKDIR /app

COPY task_file/ /tmp/task_file/
RUN mkdir -p /app/abiguard /etc/abiguard /var/lib/abiguard/baselines /var/lib/abiguard/releases/current /var/lib/abiguard/output \
    && cp -a /tmp/task_file/abiguard/. /app/abiguard/ \
    && cp /tmp/task_file/config/policy.toml /etc/abiguard/policy.toml \
    && cp /tmp/task_file/fixtures/manifests/*.abi /var/lib/abiguard/baselines/ \
    && bash /tmp/task_file/scripts/build_fixtures.sh /tmp/task_file/fixtures/src /var/lib/abiguard/releases/current \
    && rm -rf /tmp/task_file

RUN cd /app/abiguard && make clean all
