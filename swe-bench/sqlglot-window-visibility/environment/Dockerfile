FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7
RUN apt-get update -qq && apt-get install -y --no-install-recommends git=1:2.39.5-0+deb12u3 && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 solver
COPY base.bundle /tmp/base.bundle
RUN git clone /tmp/base.bundle /app && git -C /app checkout --detach 0ebcabfcb4edfbbdd18a4fa1a38485a59a5f6d7f && rm /tmp/base.bundle && chown -R solver:solver /app
RUN pip install --no-cache-dir duckdb==1.4.3 numpy==2.2.6 pandas==2.2.3 python-dateutil==2.9.0.post0 pytz==2026.5 tzdata==2026.5 six==1.17.0 ruff==0.15.6 typing-extensions==4.16.0
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN mkdir -p /logs/agent /logs/artifacts /logs/verifier /tests && chmod 700 /logs/verifier /tests
