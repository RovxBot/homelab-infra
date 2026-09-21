# SkyFire 5.4.8 requires C++23/GCC 14 and Boost 1.91. Ubuntu 24.04 supplies
# the supported base platform; Boost is built from a checksum-pinned archive.
ARG UBUNTU_VERSION=24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3

FROM ubuntu:${UBUNTU_VERSION} AS build
ARG DEBIAN_FRONTEND=noninteractive
ARG BOOST_VERSION=1.91.0
ARG BOOST_ARCHIVE=boost_1_91_0
ARG BOOST_SHA256=03ad1f3a0ad626837e59ce693b5cabe3385949319a40da5535b68716fafc918d
ARG OPENSSL_VERSION=4.0.1
ARG OPENSSL_SHA256=2db3f3a0d6ea4b59e1f094ace2c8cd536dffb87cdc39084c5afa1e6f7f37dd09

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl bzip2 build-essential gcc-14 g++-14 cmake ninja-build \
        default-libmysqlclient-dev libssl-dev libreadline-dev zlib1g-dev libbz2-dev \
        libncurses-dev libjemalloc-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl --fail --silent --show-error --location --output /tmp/boost.tar.bz2 \
        "https://archives.boost.io/release/${BOOST_VERSION}/source/${BOOST_ARCHIVE}.tar.bz2" \
    && echo "${BOOST_SHA256}  /tmp/boost.tar.bz2" | sha256sum --check \
    && tar -xjf /tmp/boost.tar.bz2 -C /tmp \
    && cd "/tmp/${BOOST_ARCHIVE}" \
    && ./bootstrap.sh --prefix=/opt/boost \
    && ./b2 --with-system --with-filesystem --with-program_options \
        link=static runtime-link=static variant=release install \
    && rm -rf /tmp/boost.tar.bz2 "/tmp/${BOOST_ARCHIVE}"

RUN curl --fail --silent --show-error --location --output /tmp/openssl.tar.gz \
        "https://www.openssl.org/source/openssl-${OPENSSL_VERSION}.tar.gz" \
    && echo "${OPENSSL_SHA256}  /tmp/openssl.tar.gz" | sha256sum --check \
    && tar -xzf /tmp/openssl.tar.gz -C /tmp \
    && cd "/tmp/openssl-${OPENSSL_VERSION}" \
    && ./Configure linux-x86_64 --prefix=/opt/openssl --libdir=lib shared \
    && make -j"$(nproc)" \
    && make install_sw \
    && rm -rf /tmp/openssl.tar.gz "/tmp/openssl-${OPENSSL_VERSION}"

COPY . /src
WORKDIR /src/build

RUN cmake -G Ninja /src \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/skyfire \
        -DBOOST_ROOT=/opt/boost \
        -DOPENSSL_ROOT_DIR=/opt/openssl \
        -DCMAKE_C_COMPILER=gcc-14 \
        -DCMAKE_CXX_COMPILER=g++-14 \
        -DMODULES=ON -DTOOLS=ON -DWITH_WARNINGS=OFF -DWITHOUT_GIT=ON \
    && cmake --build . --parallel "$(nproc)" \
    && cmake --install .

FROM ubuntu:${UBUNTU_VERSION} AS runtime
ARG DEBIAN_FRONTEND=noninteractive

# Ubuntu 24.04 reserves UID/GID 1000 for its built-in ubuntu account.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates tzdata default-mysql-client libmysqlclient21 libssl3 libreadline8 \
        libjemalloc2 libbz2-1.0 libncursesw6 zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 skyfire \
    && useradd --uid 10001 --gid skyfire --create-home --home-dir /opt/skyfire skyfire

COPY --from=build --chown=10001:10001 /opt/skyfire /opt/skyfire
COPY --from=build /opt/openssl /opt/openssl
# The upstream repo deliberately excludes the large world base dump. GitHub
# Actions verifies the official release archive before it enters the context.
COPY --from=build --chown=10001:10001 /src/sql /opt/skyfire/sql

RUN echo /opt/openssl/lib > /etc/ld.so.conf.d/skyfire-openssl.conf && ldconfig

ENV TZ=Australia/Adelaide
USER skyfire
WORKDIR /opt/skyfire
CMD ["/opt/skyfire/bin/worldserver"]
