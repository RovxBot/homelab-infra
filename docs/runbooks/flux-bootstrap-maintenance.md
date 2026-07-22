# Flux bootstrap maintenance

`clusters/home/flux-system/gotk-components.yaml` is generated control-plane
material. It is deliberately excluded from Renovate's Flux manager. Renovate
continues to update Flux custom resources in `apps/`, `infra/` and `clusters/`,
but a Flux controller upgrade is a separate, reviewed maintenance change.

## Upgrade workflow

1. Select and review a Flux release, then generate a candidate bootstrap
   manifest in a temporary location with the same controller components as the
   current cluster.
2. Replace `gotk-components.yaml` only with that generated output. Keep the
   repository's `kustomization.yaml` patches and `gotk-sync.yaml` reviewable;
   do not fold local policy changes into generated controller YAML.
3. Retain an exact lowercase SHA-256 digest for every `ghcr.io/fluxcd/`
   controller image. The repository gate verifies this:

   ```bash
   python3 scripts/ci/check_flux_bootstrap_digests.py \
     clusters/home/flux-system/gotk-components.yaml
   ```

4. Render `clusters/home`, review the complete controller diff, and record a
   rollback plan in the pull request.
5. After merge, confirm every Flux Kustomization and HelmRelease is Ready
   before changing another control-plane component.

Never remove a controller digest merely to make a routine dependency update
easier. Regenerating the bootstrap manifest is a deliberate Flux maintenance
operation, not a Renovate update.
