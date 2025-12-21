# wow (private game server scaffold)

This directory provides a basic Kubernetes scaffold for a private, LAN-only TCP game server + MariaDB backend:

- `wow` namespace
- `wow-mariadb` StatefulSet (Longhorn PVC)
- `wow-server` Deployment + NodePort Service (placeholder image and ports)

## Before enabling

1. Update the server image in `apps/wow/server-deployment.yaml` to the container image you intend to run.
2. Set the exposed ports in `apps/wow/server-deployment.yaml` and `apps/wow/server-service.yaml` to match your server software.
3. Unsuspend Flux Kustomization `apps-wow` (it is shipped as `suspend: true` so nothing deploys until you’re ready).

## Flux

The Flux Kustomization is defined in `clusters/home/apps/wow-kustomization.yaml`.

