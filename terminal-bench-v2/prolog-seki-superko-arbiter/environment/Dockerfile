FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

# No canonical Prolog runtime image is available in the local profile; this pinned Python slim image provides a small Debian base for SWI-Prolog and the pytest verifier.

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        swi-prolog-nox \
        tmux \
    && python -m pip install --no-cache-dir \
        pytest==8.4.1 \
        pytest-json-ctrf==0.3.5 \
    && rm -rf /var/lib/apt/lists/*

COPY src/ /app/src/
COPY scripts/ /app/scripts/
RUN chmod 0755 /app/scripts/build.sh

WORKDIR /app
