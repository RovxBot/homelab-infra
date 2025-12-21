FROM ubuntu:24.04 AS build

ARG DEBIAN_FRONTEND=noninteractive
ARG MANGOSZERO_REF=master

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    cmake \
    ninja-build \
    build-essential \
    pkg-config \
    libssl-dev \
    zlib1g-dev \
    libbz2-dev \
    libreadline-dev \
    libmariadb-dev-compat \
    libmariadb-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --recurse-submodules --depth=1 --branch "${MANGOSZERO_REF}" https://github.com/mangoszero/server.git /src/mangoszero

WORKDIR /src/mangoszero
RUN cmake -S . -B build -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/opt/mangos
RUN cmake --build build
RUN cmake --install build

FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libssl3 \
    zlib1g \
    libbz2-1.0 \
    libreadline8t64 \
    liblua5.2-0 \
    libmariadb3 \
    gettext-base \
    tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/mangos /opt/mangos
COPY ops/wow-images/entrypoint.sh /entrypoint.sh

RUN useradd -u 10001 -m -s /bin/bash mangos \
  && mkdir -p /data /config \
  && chown -R mangos:mangos /data /config /opt/mangos /entrypoint.sh \
  && chmod +x /entrypoint.sh

USER mangos
WORKDIR /data

ENTRYPOINT ["/usr/bin/tini","--","/entrypoint.sh"]
