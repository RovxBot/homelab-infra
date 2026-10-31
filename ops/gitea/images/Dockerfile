FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852

# Snapshot repositories keep rebuilds independent of GitHub and package churn.
ARG UBUNTU_SNAPSHOT=20261030T000000Z
ARG NODE_VERSION=24.21.0
ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-euxo", "pipefail", "-c"]
RUN sed -i -E "s@https?://(archive|security).ubuntu.com/ubuntu/?@http://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT}/@g" /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl xz-utils bash coreutils tar git git-lfs openssh-client \
       python3 python3-venv build-essential clang-18 libclang-rt-18-dev \
       cmake ninja-build pkg-config jq zip unzip \
       libsdl2-dev libwayland-dev wayland-protocols libxkbcommon-dev libdecor-0-dev \
       libegl-dev libgles-dev libgbm-dev libx11-dev libxext-dev libxrandr-dev \
       libxcursor-dev libxfixes-dev libxi-dev libxss-dev libgl-dev \
       libgl1-mesa-dri mesa-utils xvfb xauth \
    && ln -s /usr/bin/clang-18 /usr/local/bin/clang \
    && ln -s /usr/bin/clang++-18 /usr/local/bin/clang++ \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" -o /tmp/node.tar.xz \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" -o /tmp/node-sums \
    && expected="$(awk -v name="node-v${NODE_VERSION}-linux-x64.tar.xz" '$2 == name {print $1}' /tmp/node-sums)" \
    && test "${#expected}" = 64 \
    && echo "$expected  /tmp/node.tar.xz" | sha256sum -c - \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
    && dpkg-query -W > /usr/local/share/toolchain-packages.txt \
    && rm -rf /var/lib/apt/lists/* /tmp/node* \
    && mkdir -p /workspace /home/ubuntu \
    && chown -R 1000:1000 /workspace /home/ubuntu

# Builds have no sudo, service account token, Docker socket or host mounts.
USER 1000:1000
ENV HOME=/home/ubuntu LIBGL_ALWAYS_SOFTWARE=1
WORKDIR /workspace
CMD ["bash"]
