FROM golang:1.26-trixie@sha256:cff05dd6b81c901f2e8249eb6f91c69a60451d0f12c26bfef08e476a85b32d4d AS build
WORKDIR /work
ENV CGO_ENABLED=0
COPY upstream/ ./
COPY force_rescan_test.go core/services/homelab_force_rescan_test.go
RUN go test ./core/services -run 'TestScanCVEHomelabForceRescan|TestScanService_ScanCVE_ReportTimestamp' -count=1
RUN go build -trimpath -o /out/kubevuln ./cmd/http

# Preserve the qualified runtime filesystem and entrypoint; replace only the
# engine binary built from the exact qualified source plus the reviewed patch.
FROM quay.io/kubescape/kubevuln:v0.3.578@sha256:925ced171a8d262e95ea72c04b0ebfec43d9128dbe4d875e783b80df56e54416
COPY --from=build /out/kubevuln /usr/bin/kubevuln
LABEL org.opencontainers.image.source="https://github.com/RovxBot/homelab-infra"
LABEL homelab.cooked.beer/upstream="45d265e3b46af4d7fc1c896ecc22a93915f2d6fe"
LABEL homelab.cooked.beer/patch="authenticated-force-rescan-v1"
