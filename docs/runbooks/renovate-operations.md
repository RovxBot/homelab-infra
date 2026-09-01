# Renovate operations

The hosted Renovate GitHub App is the sole dependency-update runner for this
repository. It owns the Dependency Dashboard and creates reviewable PRs.
There is intentionally no scheduled Renovate GitHub Actions workflow.

Renovate creates at most three open dependency branches/PRs and at most two
new PRs per hour. This keeps the review queue aligned with the single-owner
maintenance cadence. Security PRs can still bypass the normal concurrent limit.
The limits do not close existing PRs; triage or close those explicitly from the
Dashboard.

Routine updates wait for approval on the Dependency Dashboard instead of
consuming an open-PR slot. Renovate batches every supported application and its
supporting images into that application's update PR, including a major
application release with its available dependency changes. Approve the
application's Dashboard entry when it is time to do that maintenance; the
Dashboard remains the complete queue even while the PR limits are full.

GitHub vulnerability alerts are the exception: Renovate opens their remediation
PRs immediately, without Dashboard approval and without applying the normal
branch, PR, hourly, or schedule limits. They carry the `security` label and may
be reviewed independently of the application batch.

All Renovate automerge is disabled. A generated PR is a prompt for review, not
permission to deploy it. Check the manifest or Terraform diff, CI status,
release notes and any relevant maintenance boundary before merging.

## Triage

1. Start at the Dependency Dashboard. When capacity is available, approve an
   application batch rather than a one-off supporting-image update.
2. Require a maintenance plan for Cilium, Talos, Kubernetes, Longhorn, GPU
   Operator, Kyverno, OCI edge and database changes.
3. Close stale PRs rather than merging a change that no longer has a clear
   source version or validation result. Renovate will recreate an eligible
   update from the current base.
4. After a merged GitOps change, verify Flux and the affected workload before
   selecting the next update.

`main` requires branches to be current before merge, so Renovate deliberately
rebases a dependency PR after `main` advances. Treat the regenerated commit as
a fresh review: wait for CI again and renew the required code-owner approval.

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
