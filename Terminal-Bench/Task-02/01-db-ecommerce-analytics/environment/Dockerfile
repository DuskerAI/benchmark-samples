FROM python:3.11-slim@sha256:233de06753d30d120b1a3ce359d8d3be8bda78524cd8f520c99883bfe33964cf

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget=1.25.0-2 \
    ca-certificates=20250419 \
    gcc=4:14.2.0-1 \
    g++=4:14.2.0-1 \
    build-essential=12.12 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Go 1.22
RUN wget -q https://go.dev/dl/go1.22.5.linux-amd64.tar.gz -O /tmp/go.tar.gz \
    && tar -C /usr/local -xzf /tmp/go.tar.gz \
    && rm /tmp/go.tar.gz
ENV PATH="/usr/local/go/bin:$PATH"
ENV GOPATH="/root/go"
ENV GONOSUMDB="*"
ENV GOFLAGS="-mod=mod"

# Pre-cache go-duckdb so agents can use it without network access at solve time
RUN mkdir -p /tmp/go-warmup && cd /tmp/go-warmup \
    && go mod init warmup \
    && go get github.com/marcboeker/go-duckdb@v1.6.3 \
    && cd / && rm -rf /tmp/go-warmup

WORKDIR /app
COPY task_file/ /app/
