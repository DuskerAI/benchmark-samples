FROM public.ecr.aws/docker/library/golang:1.25.3-bookworm@sha256:4f43b271f9673eb7bd0cb3a49cc17b08d8d6ee110277e26dbacc93c43a5a7793
ENV GOTOOLCHAIN=local CGO_ENABLED=0 GOMAXPROCS=2 GOMODCACHE=/opt/gomod GOCACHE=/opt/gocache PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN python3 --version && git --version && curl --version
COPY base.bundle /tmp/base.bundle
RUN git clone /tmp/base.bundle /app && git -C /app checkout --detach 8290e0a52ebcb5225a2dcfe154dff5b97054481f && rm /tmp/base.bundle
WORKDIR /app
RUN go mod download && go test -p 2 -run '^$' ./workflow/... ./internal/... ./backend/sqlite ./backend/history
RUN useradd --create-home --uid 1000 solver
ENV GOPROXY=off GOSUMDB=off GOCACHE=/tmp/go-build-cache
RUN cp -a /opt/gocache /tmp/go-build-cache && chown -R solver:solver /app /tmp/go-build-cache && mkdir -p /logs/agent /logs/artifacts /logs/verifier /tests && chmod 700 /logs/verifier /tests
