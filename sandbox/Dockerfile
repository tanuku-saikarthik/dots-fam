# The throwaway container build-mode Dots run commands in: Python 3.12, Node 22, git, a C toolchain.
#   docker build -t dotsfam-build sandbox/
# Need something else (Go, Rust, Java)? Add it here, or point BUILD_IMAGE at any image with sh.
FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git build-essential \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv pytest \
 && npm install -g pnpm

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /work
