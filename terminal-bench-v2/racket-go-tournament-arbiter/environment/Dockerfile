FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        tmux \
    && rm -rf /var/lib/apt/lists/*

# Install the pinned Minimal Racket tarball instead of Debian's large racket
# package; this keeps platform environment startup under the 900s AutoEval cap.
RUN python -c "import urllib.request; u='https://download.racket-lang.org/releases/8.14/installers/racket-minimal-8.14-x86_64-linux-cs.tgz'; r=urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0'}); open('/tmp/racket.tgz','wb').write(urllib.request.urlopen(r).read())" \
    && echo 'a1b5cab3adee21cd00219c7e85315c945db4eab11a6c80653db7687b11c993f0  /tmp/racket.tgz' | sha256sum -c - \
    && mkdir -p /opt \
    && tar -xzf /tmp/racket.tgz -C /opt \
    && ln -s /opt/racket/bin/racket /usr/local/bin/racket \
    && ln -s /opt/racket/bin/raco /usr/local/bin/raco \
    && rm /tmp/racket.tgz

RUN python -m pip install --no-cache-dir \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

WORKDIR /app
COPY arbiter.rkt /app/arbiter.rkt
