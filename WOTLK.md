# WotLK Server Stack

This repo contains my WotLK (AzerothCore + Playerbots) deployment and related tooling. It is designed for my homelab and may require adjustments for other environments.

## What is included

- Auth server, world server, and MariaDB statefulset
- World/auth configuration templates
- CronJobs for realm updates, bot maintenance, and custom content
- Optional account management portal assets

Key manifests live under:

- `apps/wotlk/worldserver-deployment.yaml`
- `apps/wotlk/authserver-deployment.yaml`
- `apps/wotlk/mariadb-statefulset.yaml`
- `apps/wotlk/playerbots-mcp-deployment.yaml`
- `apps/wotlk/playerbots-mcp-service.yaml`
- `apps/wotlk/kustomization.yaml` (ConfigMap generator for worldserver config)
- `apps/wotlk/authserver-config.yaml`
- `apps/wotlk/db-bootstrap-cronjob.yaml`

## Prebuilt images

Images are published to GHCR under:

- `ghcr.io/rovxbot/homelab-infra/azerothcore-wotlk`

Tags vary by build and component. The deployments in `apps/wotlk/` reference the exact image tags used by this cluster. To update:

1. Build/publish new images.
2. Update the image tags in:
   - `apps/wotlk/worldserver-deployment.yaml`
   - `apps/wotlk/authserver-deployment.yaml`
   - `apps/wotlk/db-bootstrap-cronjob.yaml` (if used)
3. Commit + push to `main` so Flux applies the changes.

## Licensing and upstreams

This repo is MIT licensed (see `LICENSE`) and is free to use. Upstream components have their own licenses and must be respected:

- AzerothCore: https://github.com/azerothcore/azerothcore-wotlk
- Playerbots: https://github.com/RovxBot/mod-playerbots

When using prebuilt images or module content, ensure you comply with all upstream licenses and terms.

## Playerbots MCP server

This repo also includes an internal-only MCP deployment for C++ code navigation against the AzerothCore + Playerbots source tree.

- Image build workflow: `.github/workflows/mcp-cpp-playerbots-image.yml`
- Image source inputs: `ops/mcp-cpp/Dockerfile`, `ops/mcp-cpp/mcp-cpp.ref`
- K8s manifests: `apps/wotlk/playerbots-mcp-deployment.yaml`, `apps/wotlk/playerbots-mcp-service.yaml`

How it works:

1. The pod clones the same AzerothCore fork/ref from `wotlk-acore-source`.
2. It clones any extra module repos listed in `apps/wotlk/config/modules.txt`.
3. It runs a CMake configure step to generate `compile_commands.json`.
4. It starts `mcp-cpp-server` behind `supergateway` and exposes Streamable HTTP on port `8000` at `/mcp`.

Recommended access pattern from your workstation:

```bash
kubectl -n wotlk port-forward svc/wotlk-playerbots-mcp 8000:8000
```

Then point VS Code at the forwarded endpoint:

```json
{
  "servers": {
    "playerbots-cpp": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Suggested rollout order:

1. Run the `Build Playerbots MCP image` workflow so `ghcr.io/<owner>/mcp-cpp-playerbots:main` exists.
2. Commit and push this repo change.
3. Reconcile `apps-wotlk` in Flux or wait for the normal sync.
4. Port-forward the service locally and add the VS Code MCP entry.
