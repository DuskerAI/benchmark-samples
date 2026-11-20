FROM public.ecr.aws/docker/library/node@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS node
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
RUN apt-get update -qq && apt-get install -y --no-install-recommends git=1:2.39.5-0+deb12u3 make=4.3-4.1 g++=4:12.2.0-3 && rm -rf /var/lib/apt/lists/*
RUN npm install -g pnpm@9.12.0
RUN useradd --create-home --uid 1000 solver
COPY base.bundle /tmp/base.bundle
RUN git clone /tmp/base.bundle /app && git -C /app checkout --detach 995e375eb72e64b920e5c432590fb7a924cc9031 && rm /tmp/base.bundle
WORKDIR /app
RUN pnpm install --frozen-lockfile --store-dir /opt/pnpm-store
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN chown -R solver:solver /app && mkdir -p /logs/agent /logs/artifacts /logs/verifier /tests && chmod 700 /logs/verifier /tests
