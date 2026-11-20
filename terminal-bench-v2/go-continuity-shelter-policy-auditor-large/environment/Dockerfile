# Canonical Terminal-Bench Go base image, digest-pinned for reproducible builds.
FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

RUN apt-get update && apt-get install -y --no-install-recommends \
    asciinema=2.2.0-1 \
    ca-certificates=20230311+deb12u1 \
    curl=7.88.1-10+deb12u14 \
    git=1:2.39.5-0+deb12u3 \
    procps=2:4.0.2-3 \
    python3=3.11.2-1+b1 \
    python3-pip=23.0.1+dfsg-1 \
    tmux=3.3a-3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /tmp/requirements.lock

# The lock includes pytest-json-ctrf for the verifier's --ctrf report flag.
RUN pip3 install --no-cache-dir --break-system-packages \
    --require-hashes --no-deps -r /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock

WORKDIR /app/task_file

COPY task_file/ /app/task_file/
COPY policy-corpus/ /opt/continuity-policy-corpus/
COPY cases/ /var/lib/continuity-audit/cases/
COPY config/config.json /etc/continuity-audit/config.json

RUN ln -sf /usr/local/go/bin/go /usr/local/bin/go \
    && mkdir -p /var/lib/continuity-audit/output /logs/verifier /app/task_file/bin \
    && command -v tmux >/dev/null \
    && command -v asciinema >/dev/null \
    && go test ./...
