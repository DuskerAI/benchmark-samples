FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        tmux \
        asciinema \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/verifier \
    && /opt/verifier/bin/pip install --no-cache-dir \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

ENV PATH="/opt/verifier/bin:/usr/local/go/bin:${PATH}"
RUN printf '%s\n' 'export PATH="/opt/verifier/bin:/usr/local/go/bin:${PATH}"' \
    > /etc/profile.d/tb2-go-path.sh

WORKDIR /app
COPY README.md /app/README.md
COPY task_file/ /app/
RUN chmod +x /app/bin/gatewayctl
RUN go mod download
