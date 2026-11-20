FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7
RUN apt-get update -qq && apt-get install -y --no-install-recommends git=1:2.39.5-0+deb12u3 && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 solver
COPY base.bundle /tmp/base.bundle
RUN git clone /tmp/base.bundle /app && git -C /app checkout --detach 0ebcabfcb4edfbbdd18a4fa1a38485a59a5f6d7f && rm /tmp/base.bundle
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY . /tests/
COPY api_driver.py /opt/window-api-driver.py
RUN mkdir -p /sandbox/app /sandbox/tmp /sandbox/opt /sandbox/dev && cp -a /usr /bin /lib /etc /sandbox/ && if [ -e /lib64 ]; then cp -a /lib64 /sandbox/; fi && cp /opt/window-api-driver.py /sandbox/opt/ && chmod 1777 /sandbox/tmp && find /sandbox -type f -perm /6000 -exec chmod a-s {} + && mknod -m 666 /sandbox/dev/null c 1 3
RUN find /sandbox -name "_sqlite3*.so" -delete && find /sandbox -name "libsqlite3.so*" -delete
RUN chown -R root:root /app /tests /opt/window-api-driver.py && chmod -R go-rwx /tests && chmod 444 /opt/window-api-driver.py && mkdir -p /logs/verifier && chmod 700 /logs/verifier
USER root
