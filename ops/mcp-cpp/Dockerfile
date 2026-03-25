FROM rust:1.88-bookworm AS builder

ARG MCP_CPP_REF=main

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN git clone --depth=1 --branch "${MCP_CPP_REF}" https://github.com/mpsm/mcp-cpp.git .
RUN cargo build --release


FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates clangd git tini \
    && npm install -g supergateway \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /src/target/release/mcp-cpp-server /usr/local/bin/mcp-cpp-server

WORKDIR /workspace/source

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["supergateway", "--stdio", "mcp-cpp-server --root /workspace/source --clangd-path /usr/bin/clangd", "--outputTransport", "streamableHttp", "--port", "8000", "--streamableHttpPath", "/mcp", "--stateful", "--sessionTimeout", "3600000"]
