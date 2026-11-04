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

Minor updates to the digest-pinned `docker/dockerfile` frontend and Composer in
`ops/grimguzzler-registration/Dockerfile` also join weekly maintenance without
Dashboard approval. PHP runtime line upgrades still need approval. The
registration workflow builds the image and runs its smoke tests on the weekly
PR when that Dockerfile changes.

Gitea application, Helm chart, runner and mirror minor releases also join the
Monday batch, alongside their routine patches and digest updates. Major
component upgrades, PostgreSQL majors and toolchain OS/runtime upgrades remain
outside this exception. Take a verified cold snapshot and review Gitea's
migration/recovery notes before merging a batch containing Gitea changes.

BirdNET's stable eight-digit `YYYYMMDD` image tags join weekly maintenance,
including date changes Renovate's Docker versioning labels as major. This is
an explicit exception for that release channel, not a compatibility claim.
Floating, prerelease and semantic-version tags are excluded. Both the init
container and main container must move together. Check the dashboard reader's
schema compatibility as described in the [BirdNET runbook](birdnet-go.md).

Other minor and major releases retain their existing per-application groups and
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

Application release titles set both `commitMessageTopic` and
`group.commitMessageTopic`. Renovate applies the group template even when
`groupSingleUpdates` produces a one-package batch; without that override, a
Jellyfin-only release appears as `update jellyfin` and the approval workflow
cannot identify `jellyfin/jellyfin`. Multiple-package entries also list their
packages, so the primary image remains detectable when a helper supplies the
title. The weekly rule overrides both templates to keep its usual batch title.
After this correction reaches `main`, let the hosted App regenerate the
Dashboard titles; that edit triggers application-release approval.

Cluster-control components (Cilium, Flux bootstrap, Kyverno, Longhorn, GPU
Operator and snapshot-controller) stay out of weekly maintenance. Python
helper images in these four manifests are narrow exceptions:

- `infra/kyverno/automation/github-issue-sync-cronjob.yaml`
- `infra/longhorn/weekly-backup-retention.yaml`
- `infra/observability/gatus-github-issue-sync-cronjob.yaml`
- `ops/longhorn/reviewed-backup-retention-job.yaml`

Their minor, patch, digest and pin updates join the Monday batch without
Dashboard approval. Major Python upgrades still require approval. This exception
does not cover CI language runtime inputs or application runtimes.

Manifest validation runs the Longhorn retention and engine-guard tests plus
issue-sync smoke checks in every distinct digest-pinned Python helper image
from the PR's manifests. Containers use a read-only checkout, no network and
no live credentials. Changes to the manual retention Job also select this check.
These checks exercise runtime compatibility; they do not contact the cluster
or validate a live backup deletion. All updates still need manual merge review.

Snapshot-controller has its own group and Dashboard approval gate for every
update type, including patches and digests. Review the upstream CRDs and RBAC
alongside its image; the vendored YAML does not update with an image bump.
Terraform remains in its own group.
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

Major GitHub Actions updates, including runner-image upgrades detected by the
GitHub Actions manager, bypass Dashboard approval and open separately in the
`major-github-actions` group. They do not wait for Monday, and still require
successful CI and manual merge review. Routine Action patch/digest/pin updates
join weekly maintenance; minor Action updates retain Dashboard approval.

Language runtime inputs such as `python-version` and `node-version` are
distinct from Action versions. Their minor and major upgrades in general CI
form a separate `ci-runtimes` group and require Dashboard approval. Routine
runtime patches retain the weekly maintenance schedule. Home Assistant's
regression workflow is constrained to Python 3.14 to match the deployed release;
review that constraint alongside a Home Assistant release that changes its
Python runtime. Do not approve an old combined Python entry before the hosted
App has reconciled these rules and excluded the Home Assistant workflow.

The repository explicitly allows `ubuntu-26.04` in `.github/actionlint.yaml`
because the pinned actionlint 1.7.8 does not recognise that GitHub-hosted label.
Home Assistant CI has already passed on this runner. Remove the allowance when
the pinned linter includes it. Changes to either supported actionlint config
filename select workflow validation. After this fix reaches `main`, request a
rebase of the outstanding major Actions PR and wait for checks on the new head;
retrying its old head will still use the old linter configuration.

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

GitHub's **Update branch** button can add a human-authored merge commit and put
a Renovate PR in **PR Edited (Blocked)**. Prefer Renovate's rebase/retry control.
Before resetting an already blocked PR, inspect its commits and diff: the retry
can discard manual commits, so preserve any intentional edits first. A PR in
**PR Closed (Blocked)** instead needs its Dashboard recreate checkbox when the
update is wanted again; it is not a package lookup error.

The consolidated PR workflow retains all five required status-check names and
only allocates expensive jobs for relevant changes. A scheduled/manual audit
checks the full repository; the validation workflows no longer repeat on every
merge. Flux still reconciles reviewed changes from `main`.

## Required approval gates

The Dependency Dashboard must be explicitly approved before Renovate opens a
PR for:

- Kyverno, Longhorn and GPU Operator Helm chart changes.
- Snapshot-controller image changes, including patches and digests.
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

## Gitea image and chart tracking

The digest-only `ghcr.io/rovxbot/gitea-linux` and
`ghcr.io/rovxbot/gitea-backup` references are pipeline outputs, not upstream
release channels. Renovate is disabled only for these packages in
`apps/gitea/**` and `apps/gitea-runners/**`. Their Dockerfile base images remain
tracked. The Gitea Linux toolchain workflow builds and smoke-tests both images,
publishes revision tags and records immutable digests in its
`gitea-toolchain-digest` artifact. Promote those digests together across runner
configuration, admission policy, backup/synchronization jobs and restore tools
after checking recovery compatibility. There is no floating `latest` tag to
follow; adding one just to silence a lookup warning would change this model.

Gitea's Helm chart appends `-rootless` to `values.image.tag` and then appends
the separate `values.image.digest`. The custom regex manager therefore looks
up `docker.gitea.com/gitea:<version>-rootless` and updates the plain tag and
digest fields together. The generic Flux Docker extraction is disabled only
for this image in this HelmRelease; it otherwise ignores the rootless suffix
and separate digest, producing an invalid rendered reference when it pins a
digest inside the tag. Gitea Helm chart tracking remains enabled. Its source
manifest declares namespace `gitea`, matching both the source reference and
the namespace already applied by Kustomize, so Renovate can resolve the chart
repository before rendering.

After these rules reach `main`, request a rebase/retry of the open weekly PR
so the old Gitea tag-only pin is replaced by the rootless tag/digest update.
Its next ordinary refresh is on Monday; a manual rebase request can refresh it
earlier. Review the generated diff and fresh CI before merging.

## Credentials and runner hygiene

The retired Actions runner used `RENOVATE_TOKEN`, `DOCKERHUB_USERNAME` and
`DOCKERHUB_TOKEN`. Once those obsolete repository secrets and the old Renovate
PAT have been removed, do not recreate them for routine Renovate operation.
The hosted App is the supported integration.

If Renovate reports a lookup failure, investigate the exact package and scope
it narrowly in `renovate.json`; do not disable an entire application tree just
to silence one dependency.
