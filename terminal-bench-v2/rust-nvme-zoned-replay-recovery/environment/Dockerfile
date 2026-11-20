FROM public.ecr.aws/docker/library/rust:1.85-slim@sha256:9f841bbe9e7d8e37ceb96ed907265a3a0df7f44e3737d0b100e7907a679acb36

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        tmux \
        asciinema \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages --no-cache-dir \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

COPY nvmez-recover/ /opt/nvmez-recover/

RUN python3 /opt/nvmez-recover/tools/gen_cases.py \
    && cd /opt/nvmez-recover \
    && cargo build --release --locked \
    && rm -rf target

WORKDIR /opt/nvmez-recover
