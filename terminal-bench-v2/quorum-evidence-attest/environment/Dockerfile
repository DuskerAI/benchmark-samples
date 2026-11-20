# Canonical TB2 Debian base. Exception: Erlang is installed from apt because the canonical TB2 image list has no Erlang-specific runtime image.
FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        erlang-nox \
        python3 \
        python-is-python3 \
        python3-pip \
        tmux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --break-system-packages \
    --require-hashes -r /tmp/requirements.txt

COPY --chmod=0644 spec.md /usr/share/qguard/spec.md
COPY rootfs/ /
COPY --chmod=0755 rootfs/usr/local/sbin/qguard-attest /usr/local/sbin/qguard-attest

WORKDIR /var/lib/qguard
