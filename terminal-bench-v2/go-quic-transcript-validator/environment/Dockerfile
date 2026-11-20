# Canonical Terminal-Bench Go base image.
FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        python3 \
        python3-pip \
        tmux \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --break-system-packages pytest==8.4.1

COPY go.mod /app/quicval/go.mod
COPY cmd/ /app/quicval/cmd/
COPY internal/ /app/quicval/internal/
COPY docs/ /app/quicval/docs/
COPY tools/ /tmp/quicval-tools/

RUN mkdir -p /app/quicval/bin /app/quicval/fixtures /app/quicval/out \
    && go run /tmp/quicval-tools/make_fixtures.go -out /app/quicval/fixtures \
    && rm -rf /tmp/quicval-tools \
    && cd /app/quicval \
    && go test ./...
