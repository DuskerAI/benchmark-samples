FROM node:20.18.1-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    make \
    cmake \
    python3 \
    python3-pip \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app/ /app/

RUN cd /app/server && npm ci
RUN cd /app/frontend && npm ci

RUN python3 /app/data/generate_data.py

RUN mkdir -p /app/build
