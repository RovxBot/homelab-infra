# Contributing

Thanks for the interest. This repo is a personal homelab learning project, so changes are intentionally conservative.

## Workflow

1. Fork and create a feature branch.
2. Keep changes small and focused.
3. Open a PR against `main` with a clear summary and testing notes.

`Repo Checks` chooses validation from the PR's changed files. Secret scanning
runs for every PR; manifests, workflows, Terraform roots, operational scripts
and Renovate configuration run their relevant checks. CI workflow or selector
changes run the complete suite. Full repository audits also run weekly and can
be started manually. See [CI and policy gates](README.md#ci-and-policy-gates).

Before merging an outdated Renovate PR, use its rebase/retry checkbox or the
`rebase` label, then wait for fresh CI. Routine patch/digest updates share one
Monday maintenance PR; see [Renovate operations](docs/runbooks/renovate-operations.md).

## Guidelines

- Do not add plaintext secrets. Use SOPS for anything sensitive.
- Avoid committing large binaries or backups.
- Keep manifests readable and consistent with existing structure.
- If you add an app, include a `kustomization.yaml` and wire it into the relevant Flux Kustomization.

## What gets accepted

- Fixes that improve reliability or clarity.
- Small, well-scoped improvements to manifests or docs.
- Reasonable defaults that do not assume a specific environment.
