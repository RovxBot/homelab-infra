ARG UBUNTU_VERSION=22.04 # lts

FROM ubuntu:$UBUNTU_VERSION AS skeleton

ARG TZ=Etc/UTC
ARG DOCKER=1
ARG DEBIAN_FRONTEND=noninteractive

ENV AC_FORCE_CREATE_DB=1

RUN printf '%s\n' \
        'Acquire::Retries "5";' \
        'Acquire::http::Timeout "30";' \
        'Acquire::https::Timeout "30";' \
        > /etc/apt/apt.conf.d/80-retries

RUN printf '%s\t%s\n' \
        'http://azure.archive.ubuntu.com/ubuntu/' 'priority:1' \
        'https://archive.ubuntu.com/ubuntu/' 'priority:2' \
        'https://security.ubuntu.com/ubuntu/' 'priority:3' \
        > /etc/apt/apt-mirrors.txt \
    && sed -i \
        -e 's|http://archive\.ubuntu\.com/ubuntu/|mirror+file:/etc/apt/apt-mirrors.txt|g' \
        -e 's|http://security\.ubuntu\.com/ubuntu/|mirror+file:/etc/apt/apt-mirrors.txt|g' \
        /etc/apt/sources.list

RUN printf '%s\n' \
        '#!/usr/bin/env bash' \
        'set -euo pipefail' \
        'attempt=1' \
        'max_attempts=5' \
        'while true; do' \
        '  rm -rf /var/lib/apt/lists/*' \
        '  if apt-get update; then' \
        '    exit 0' \
        '  fi' \
        '  if [ "$attempt" -ge "$max_attempts" ]; then' \
        '    exit 1' \
        '  fi' \
        '  sleep "$((attempt * 5))"' \
        '  attempt="$((attempt + 1))"' \
        'done' \
        > /usr/local/bin/apt-update \
    && chmod +x /usr/local/bin/apt-update

RUN mkdir -pv \
        /azerothcore/bin                   \
        /azerothcore/data                  \
        /azerothcore/deps                  \
        /azerothcore/env/dist/bin          \
        /azerothcore/env/dist/data/Cameras \
        /azerothcore/env/dist/data/dbc     \
        /azerothcore/env/dist/data/maps    \
        /azerothcore/env/dist/data/mmaps   \
        /azerothcore/env/dist/data/vmaps   \
        /azerothcore/env/dist/logs         \
        /azerothcore/env/dist/temp         \
        /azerothcore/env/dist/etc          \
        /azerothcore/modules               \
        /azerothcore/src                   \
        /azerothcore/build

RUN /usr/local/bin/apt-update                                            \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime                  \
    && echo "$TZ" > /etc/timezone                                        \
    && dpkg-reconfigure --frontend noninteractive tzdata                 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /azerothcore

FROM skeleton AS build

ARG CAPPS_BUILD="all"
ARG CTOOLS_BUILD="all"
ARG CTYPE="RelWithDebInfo"
ARG CCACHE_CPP2="true"
ARG CSCRIPTPCH="OFF"
ARG CSCRIPTS="static"
ARG CMODULES="static"
ARG CSCRIPTS_DEFAULT_LINKAGE="static"
ARG CWITH_WARNINGS="ON"
ARG CMAKE_EXTRA_OPTIONS=""
ARG GIT_DISCOVERY_ACROSS_FILESYSTEM=1

ARG CCACHE_DIR="/ccache"
ARG CCACHE_MAXSIZE="1000MB"
ARG CCACHE_SLOPPINESS="pch_defines,time_macros,include_file_mtime"
ARG CCACHE_COMPRESS=""
ARG CCACHE_COMPRESSLEVEL="9"
ARG CCACHE_COMPILERCHECK="content"
ARG CCACHE_LOGFILE=""

RUN /usr/local/bin/apt-update                                          \
    && apt-get install -y --no-install-recommends                     \
        build-essential ccache libtool cmake-data make cmake clang    \
        git lsb-base curl unzip default-mysql-client openssl          \
        default-libmysqlclient-dev libboost-all-dev libssl-dev        \
        libmysql++-dev libreadline-dev zlib1g-dev libbz2-dev          \
        libncurses5-dev                                               \
    && rm -rf /var/lib/apt/lists/*

COPY CMakeLists.txt /azerothcore/CMakeLists.txt
COPY conf /azerothcore/conf
COPY deps /azerothcore/deps
COPY src /azerothcore/src
COPY modules /azerothcore/modules

ARG CACHEBUST=1

WORKDIR /azerothcore/build

RUN --mount=type=cache,id=ccache,target=/ccache,sharing=locked \
    --mount=type=bind,target=/azerothcore/.git,source=.git \
    git config --global --add safe.directory /azerothcore \
    && cmake /azerothcore \
       -DCMAKE_INSTALL_PREFIX="/azerothcore/env/dist"  \
       -DAPPS_BUILD="$CAPPS_BUILD"                     \
       -DTOOLS_BUILD="$CTOOLS_BUILD"                   \
       -DSCRIPTS="$CSCRIPTS"                           \
       -DMODULES="$CMODULES"                           \
       -DWITH_WARNINGS="$CWITH_WARNINGS"               \
       -DCMAKE_BUILD_TYPE="$CTYPE"                     \
       -DCMAKE_CXX_COMPILER="clang++"                  \
       -DCMAKE_C_COMPILER="clang"                      \
       -DCMAKE_CXX_COMPILER_LAUNCHER="ccache"          \
       -DCMAKE_C_COMPILER_LAUNCHER="ccache"            \
       -DBoost_USE_STATIC_LIBS="ON"                    \
       ${CMAKE_EXTRA_OPTIONS} \
    && cmake --build . --config "$CTYPE" -j $(($(nproc) + 1)) \
    && cmake --install . --config "$CTYPE"

# Keep module config templates in the runtime image even when a third-party
# module does not install conf/*.conf.dist through CMake.
FROM build AS module-conf-assets

RUN mkdir -p /module-conf-assets \
    && find /azerothcore/modules -type f -path '*/conf/*.conf.dist' -exec cp {} /module-conf-assets/ \;

FROM skeleton AS runtime

ARG USER_ID=1000
ARG GROUP_ID=1000
ARG DOCKER_USER=acore

ENV ACORE_COMPONENT=undefined

RUN /usr/local/bin/apt-update && \
    apt-get install -y --no-install-recommends \
      libmysqlclient21 libreadline8 \
      gettext-base default-mysql-client && \
    rm -rf /var/lib/apt/lists/*

COPY --from=build /azerothcore/env/dist/etc/ /azerothcore/env/ref/etc
COPY --from=module-conf-assets /module-conf-assets/ /azerothcore/env/ref/etc/modules/

# mod-progression-system's DatabaseScript reads bracket SQL from /modules.
# Keep that source tree in the runtime image so worldserver can apply the
# idempotent progression migration after db-import creates the world schema.
COPY --chown=$USER_ID:$GROUP_ID \
     --from=build \
     /azerothcore/modules/mod-progression-system /modules/mod-progression-system

VOLUME /azerothcore/env/dist/etc

ENV PATH="/azerothcore/env/dist/bin:$PATH"

RUN groupadd --gid "$GROUP_ID" "$DOCKER_USER" && \
    useradd -d /azerothcore --uid "$USER_ID" --gid "$GROUP_ID" "$DOCKER_USER" && \
    passwd -d "$DOCKER_USER" && \
    chown -R "$DOCKER_USER:$DOCKER_USER" /azerothcore

COPY --chown=$USER_ID:$GROUP_ID \
     --chmod=755 \
     apps/docker/entrypoint.sh /azerothcore/entrypoint.sh

USER $DOCKER_USER

ENTRYPOINT ["/usr/bin/env", "bash", "/azerothcore/entrypoint.sh"]

FROM runtime AS authserver
LABEL description="AzerothCore Auth Server"

ENV ACORE_COMPONENT=authserver
ENV AC_UPDATES_ENABLE_DATABASES=0
ENV AC_DISABLE_INTERACTIVE=1
ENV AC_CLOSE_IDLE_CONNECTIONS=0

COPY --chown=$DOCKER_USER:$DOCKER_USER \
     --from=build \
     /azerothcore/env/dist/bin/authserver /azerothcore/env/dist/bin/authserver

CMD ["authserver"]

FROM runtime AS worldserver

LABEL description="AzerothCore World Server"

ENV ACORE_COMPONENT=worldserver
ENV AC_UPDATES_ENABLE_DATABASES=0
ENV AC_DISABLE_INTERACTIVE=1
ENV AC_CLOSE_IDLE_CONNECTIONS=0

COPY --chown=$DOCKER_USER:$DOCKER_USER \
     --from=build \
     /azerothcore/env/dist/bin/worldserver /azerothcore/env/dist/bin/worldserver

VOLUME /azerothcore/env/dist/etc

CMD ["worldserver"]

FROM runtime AS db-import

LABEL description="AzerothCore Database Import tool"

USER $DOCKER_USER

ENV ACORE_COMPONENT=dbimport

COPY --chown=$DOCKER_USER:$DOCKER_USER \
    data data

COPY --chown=$DOCKER_USER:$DOCKER_USER \
    modules modules

COPY --chown=$DOCKER_USER:$DOCKER_USER \
     --from=build \
     /azerothcore/env/dist/bin/dbimport /azerothcore/env/dist/bin/dbimport

CMD ["/azerothcore/env/dist/bin/dbimport"]

FROM skeleton AS client-data

LABEL description="AzerothCore client-data"

ARG USER_ID=1000
ARG GROUP_ID=1000
ARG DOCKER_USER=acore

ENV DATAPATH=/azerothcore/env/dist/data

RUN /usr/local/bin/apt-update && \
    apt-get install -y curl unzip && \
    rm -rf /var/lib/apt/lists/*

COPY --chown=$USER_ID:$GROUP_ID apps apps

VOLUME /azerothcore/env/dist/data

USER $DOCKER_USER

CMD ["bash", "-c", "source /azerothcore/apps/installer/includes/functions.sh && inst_download_client_data"]

FROM runtime AS tools

LABEL description="AzerothCore Tools"

WORKDIR /azerothcore/env/dist/

RUN mkdir -pv /azerothcore/env/dist/Cameras \
              /azerothcore/env/dist/dbc     \
              /azerothcore/env/dist/maps    \
              /azerothcore/env/dist/mmaps   \
              /azerothcore/env/dist/vmaps

COPY --chown=$DOCKER_USER:$DOCKER_USER --from=build \
  /azerothcore/env/dist/bin/map_extractor /azerothcore/env/dist/bin/map_extractor

COPY --chown=$DOCKER_USER:$DOCKER_USER --from=build \
  /azerothcore/env/dist/bin/mmaps_generator /azerothcore/env/dist/bin/mmaps_generator

COPY --chown=$DOCKER_USER:$DOCKER_USER --from=build \
  /azerothcore/env/dist/bin/vmap4_assembler /azerothcore/env/dist/bin/vmap4_assembler

COPY --chown=$DOCKER_USER:$DOCKER_USER --from=build \
  /azerothcore/env/dist/bin/vmap4_extractor /azerothcore/env/dist/bin/vmap4_extractor
