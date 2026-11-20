FROM public.ecr.aws/docker/library/node:22-bookworm-slim@sha256:f3a68cf41a855d227d1b0ab832bed9749469ef38cf4f58182fb8c893bc462383

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python-is-python3 \
        tmux \
        asciinema \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --break-system-packages \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

WORKDIR /app/arena

COPY package.json package-lock.json tsconfig.json ./
RUN npm ci

COPY spec.md ./spec.md
COPY scripts/ ./scripts/
COPY src/ ./src/
COPY cases/ ./cases/
RUN chmod +x ./scripts/build.sh \
    && ./scripts/build.sh

WORKDIR /app/arena
