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
application release with its available dependency changes. The
`renovate-release-app-batches` workflow approves a batch only when its
Dashboard entry includes a tracked primary application image. It retries the
Dashboard while Renovate finishes dependency lookup, then Renovate creates one
PR containing that release and every pending supporting-image update in the
same application group. A supporting-image-only batch remains in the Dashboard
for manual triage.

The workflow does not run Renovate or create dependency branches itself. It has
only `issues: write`, edits the existing Dashboard checkbox, and is limited to
the primary images listed in the workflow. Add a primary image there when adding
a new application batch rule. It also scans the existing Dashboard after a
relevant change reaches `main`, so a pending application release is not left
waiting for a later Dashboard edit. This preserves the hosted Renovate GitHub
App as the sole dependency-update runner.

Terraform updates bypass Dashboard approval and are grouped across every
Terraform root and update type into one reviewable PR.

GitHub vulnerability alerts are the exception: Renovate opens their remediation
PRs immediately, without Dashboard approval and without applying the normal
branch, PR, hourly, or schedule limits. They carry the `security` label and may
be reviewed independently of the application batch.

All Renovate automerge is disabled. A generated PR is a prompt for review, not
permission to deploy it. Check the manifest or Terraform diff, CI status,
release notes and any relevant maintenance boundary before merging.

Jellyfin 12 uses a `12.0` Docker tag rather than the three-component `10.x`
form. A narrowly scoped versioning rule normalizes those tags so Renovate can
detect the major upgrade. Jellyfin 12 includes a database migration that cannot
be rolled back without restoring a backup; take a verified `/config` backup and
review the upstream migration notes before merging its PR.

## Triage

1. Start at the Dependency Dashboard. Application-release batches are approved
   automatically and open as one PR; approve a supporting-image-only batch
   manually only when it is worth a standalone maintenance change.
2. Terraform changes open as one automatic PR. Require a maintenance plan for
   Cilium, Talos, Kubernetes, Longhorn, GPU Operator, Kyverno, OCI edge and
   database changes before merging.
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
