# The canonical Java 21 JDK image is used because this Clojure task needs a JVM-backed runtime and tooling.
FROM public.ecr.aws/docker/library/eclipse-temurin:21-jdk-jammy@sha256:25d1276565738d3c805e632a4542c3a7598866ef967f4def6544c15de3a74b14

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        clojure \
        python3 \
        python-is-python3 \
        python3-pip \
        tmux \
        asciinema \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5

COPY smoother /app/smoother
RUN chmod +x /app/smoother/bin/sensor_smooth
WORKDIR /app
