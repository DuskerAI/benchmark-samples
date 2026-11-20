FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        gnat-12 \
        make \
        tmux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /tmp/requirements.lock
RUN python -m venv /opt/verifier \
    && /opt/verifier/bin/pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock \
    && chown -R root:root /opt/verifier \
    && chmod -R go-w /opt/verifier \
    && groupadd --system agent \
    && useradd --system --gid agent --home-dir /app --shell /bin/bash agent \
    && mkdir -p /app/task_file/bin /logs/verifier \
    && chown -R agent:agent /logs

COPY task_file/ /app/task_file/

WORKDIR /app
RUN make \
    -C /app/task_file \
    && chown -R root:root /app/task_file \
    && chown -R agent:agent /app/task_file/src /app/task_file/bin \
    && chmod -R u+rwX,go+rX /app/task_file \
    && chgrp agent /app/task_file \
    && chmod 1775 /app/task_file \
    && chmod -R a-w /opt/verifier

USER agent
