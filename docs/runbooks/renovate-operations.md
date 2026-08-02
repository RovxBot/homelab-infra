# Renovate operations

The hosted Renovate GitHub App is the sole dependency-update runner for this
repository. It owns the Dependency Dashboard and creates reviewable PRs.
There is intentionally no scheduled Renovate GitHub Actions workflow.

All Renovate automerge is disabled. A generated PR is a prompt for review, not
permission to deploy it. Check the manifest or Terraform diff, CI status,
release notes and any relevant maintenance boundary before merging.

## Triage

1. Start at the Dependency Dashboard and prefer isolated, low-risk digest or
   patch updates when capacity is available.
2. Require a maintenance plan for Cilium, Talos, Kubernetes, Longhorn, GPU
   Operator, Kyverno, OCI edge and database changes.
3. Close stale PRs rather than merging a change that no longer has a clear
   source version or validation result. Renovate will recreate an eligible
   update from the current base.
4. After a merged GitOps change, verify Flux and the affected workload before
   selecting the next update.

## Required approval gates

The Dependency Dashboard must be explicitly approved before Renovate opens a
PR for:

- Kyverno, Longhorn and GPU Operator Helm chart changes.
- Major OCI provider updates to the active free-tier Terraform stack.
- Any change in the independent Matrix Terraform root.
- WotLK MySQL minor updates and WotLK Ubuntu base-image line updates.

Database major updates are disabled; they require a separate migration plan.
Cilium's OCI source is deliberately disabled in Renovate because the primary
CNI runbook owns its upgrade procedure.

## WotLK boundaries

The custom `ghcr.io/rovxbot/azerothcore-wotlk` tags are build-pipeline output.
Renovate must not propose them; promote those images through the dedicated
image workflow instead.

Regular helper images in `apps/wotlk` remain tracked and are grouped for
review. This includes Alpine, Alpine Git and the digest-pinned Bitnami kubectl
image. The Bitnami reference currently uses a rolling `latest` tag plus an
immutable digest; never remove the digest merely to make the tag look pinned.

## Credentials and runner hygiene

The retired Actions runner used `RENOVATE_TOKEN`, `DOCKERHUB_USERNAME` and
`DOCKERHUB_TOKEN`. Once those obsolete repository secrets and the old Renovate
PAT have been removed, do not recreate them for routine Renovate operation.
The hosted App is the supported integration.

If Renovate reports a lookup failure, investigate the exact package and scope
it narrowly in `renovate.json`; do not disable an entire application tree just
to silence one dependency.
