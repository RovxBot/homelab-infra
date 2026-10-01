# Renovate operations

The hosted Renovate GitHub App is the sole dependency-update runner for this
repository. It owns the Dependency Dashboard and creates reviewable PRs.
There is intentionally no scheduled Renovate GitHub Actions workflow.

Renovate creates at most three open dependency branches/PRs and at most two
new PRs per hour. This keeps the review queue aligned with the single-owner
maintenance cadence. Security PRs can still bypass the normal concurrent limit.
The limits do not close existing PRs; triage or close those explicitly from the
Dashboard.

After merging this configuration, let the hosted App reconcile its Dashboard.
Existing PRs are not merged into the weekly PR automatically; review the old
queue and close superseded routine PRs if Renovate has not pruned them. Their
replacement updates are eligible for the Monday batch. Existing non-routine
PRs still occupy the three-PR limit, so the weekly PR may wait for a free slot.

Routine patch, digest and pin updates for application/infrastructure images,
non-critical charts and GitHub Actions share one `weekly maintenance` PR.
Renovate may create or refresh this batch on Mondays in `Australia/Adelaide`;
the full-day window gives the hosted App time to run. These updates do not need
Dashboard approval and are held outside Monday, including updates to an
already-open batch. One batch remains open until reviewed; this is not a new
PR every week when an earlier batch is still pending.

Minor and major releases retain their existing per-application groups and
Dashboard approval behavior. Renovate batches a supported application and its
non-routine supporting-image changes into that application's release PR. The
`renovate-release-app-batches` workflow approves a batch only when its
Dashboard entry includes a tracked primary application image. It reads the
current Dashboard once per relevant event and exits successfully without
writing when no application release needs approval. Later Dashboard edits
trigger a fresh read when new application releases appear. Renovate creates one
PR containing that release and the pending non-routine supporting-image changes
in the same application group. Routine patches/digests go into weekly
maintenance instead. A supporting-image-only release batch remains in the
Dashboard for manual triage.

Cluster-control components (Cilium, Flux bootstrap, Kyverno, Longhorn and GPU
Operator) stay out of weekly maintenance. Terraform remains in its own group.
The existing disabled database majors, primary-CNI runbook and pipeline-owned
WotLK image exclusions still apply. A digest update to a floating tag can change
runtime behavior; review the complete weekly diff before merging.

The workflow does not run Renovate or create dependency branches itself. It has
only `issues: write`, edits the existing Dashboard checkbox, and is limited to
the primary images listed in the workflow. Add a primary image there when adding
a new application batch rule. It also scans the existing Dashboard after a
relevant change reaches `main`, so a pending application release is not left
waiting for a later Dashboard edit. This preserves the hosted Renovate GitHub
App as the sole dependency-update runner.

Dashboard approval runs are serialized without canceling the active run.
Repeated edits can replace a queued run; the next run reads the current
Dashboard. There is no polling or waiting for an application release to appear.

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

1. Review the Monday `weekly maintenance` PR for routine patch/digest changes.
   Application-release batches are approved automatically on the Dependency
   Dashboard and open separately; approve a supporting-image-only release batch
   manually only when it is worth a standalone maintenance change.
2. Terraform changes open as one automatic PR. Require a maintenance plan for
   Cilium, Talos, Kubernetes, Longhorn, GPU Operator, Kyverno, OCI edge and
   database changes before merging.
3. Close stale PRs rather than merging a change that no longer has a clear
   source version or validation result. Renovate will recreate an eligible
   update from the current base.
4. After a merged GitOps change, verify Flux and the affected workload before
   selecting the next update.

Renovate uses `rebaseWhen: conflicted`, so advancing `main` does not regenerate
every open dependency PR. New dependency versions can still update a PR, and
weekly maintenance only refreshes within its Monday window. Vulnerability-fix
PRs remain immediately eligible and rebase when behind `main`.

`main` still requires branches to be current before merge. When ready to merge
an outdated PR, select Renovate's rebase/retry checkbox or add the `rebase`
label. A manual rebase request bypasses the ordinary schedule. Wait for CI on
that new commit and renew any dismissed code-owner approval before merging.
Update and merge one PR at a time to avoid triggering checks on the whole queue.

The consolidated PR workflow retains all five required status-check names and
only allocates expensive jobs for relevant changes. A scheduled/manual audit
checks the full repository; the validation workflows no longer repeat on every
merge. Flux still reconciles reviewed changes from `main`.

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
