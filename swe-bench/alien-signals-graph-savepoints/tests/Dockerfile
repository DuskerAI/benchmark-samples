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
COPY . /tests/
COPY api_driver.cjs /opt/savepoint-api-driver.cjs
RUN mkdir -p /sandbox/tmp /sandbox/opt /sandbox/dev && cp -a /usr /bin /lib /etc /app /sandbox/ && if [ -e /lib64 ]; then cp -a /lib64 /sandbox/; fi && cp /opt/savepoint-api-driver.cjs /sandbox/opt/ && chmod 1777 /sandbox/tmp && find /sandbox -type f -perm /6000 -exec chmod a-s {} + && mknod -m 666 /sandbox/dev/null c 1 3 && rm -rf /sandbox/app/.git
RUN chown -R root:root /app /tests /opt/savepoint-api-driver.cjs && chmod -R go-rwx /tests && chmod 444 /opt/savepoint-api-driver.cjs && mkdir -p /logs/verifier && chmod 700 /logs/verifier
USER root
