FROM public.ecr.aws/docker/library/golang:1.25.3-bookworm@sha256:4f43b271f9673eb7bd0cb3a49cc17b08d8d6ee110277e26dbacc93c43a5a7793
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LC_ALL=C
RUN cc --version && make --version && python3 --version && git --version
COPY base.bundle /tmp/base.bundle
RUN git clone /tmp/base.bundle /app && git -C /app checkout --detach 620a6171118fa972c3b000b7c17f2d151175cde6 && rm /tmp/base.bundle
WORKDIR /app
RUN make -j2 BUILDMODE=static XCFLAGS=-DLUA_USE_ASSERT
RUN useradd --create-home --uid 1000 solver
RUN chown -R solver:solver /app && mkdir -p /logs/agent /logs/artifacts /logs/verifier /tests && chmod 700 /logs/verifier /tests
