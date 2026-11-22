# Codex guide — RovxBot/homelab-infra

This is a **short route map**, not a checklist of files to read. The repository is a personal Talos/Flux GitOps homelab, not a generic Kubernetes template. Read **only** the files relevant to the current task. A user-supplied issue, PR, path or failure is the starting point.

## Find the relevant code without rereading the repository

- Start with `git status --short` and `git diff --stat` to avoid overwriting work. Identify the requested paths from the task/issue first.
- Narrow searches: `rg --files infra/kubescape`, `rg -n 'symbol' scripts/ci infra/kubescape`. Read specific files or line ranges, not every manifest, README or historical report.
- Do not run a full repository tree dump, recursive `cat`, blanket `find` over all content, or a complete source/history audit unless the task requires it.
- The GitHub Wiki owns current architecture/operations documentation; in-repository evidence records specific versions and acceptance snapshots. Check changed configuration or live state only when relevant. Never rediscover a validated design decision without a concrete inconsistency.
- Keep research bounded: a short hypothesis and the smallest confirming test beat repeated speculative explorations.

## Canonical documentation: GitHub Wiki

- **Wiki:** https://github.com/RovxBot/homelab-infra/wiki — primary home for architecture, component explanations, operational procedures, setup and troubleshooting. **Git repository:** `https://github.com/RovxBot/homelab-infra.wiki.git` (separate from the GitOps repo).
- When a task needs existing documentation or changes how a component is operated, consult only the matching Wiki page(s). Start from `Home.md` / `_Sidebar.md` for navigation if available; use targeted `git grep` to locate a subject, then read only those pages. Do **not** fetch/read the entire Wiki for each task.
- If no Wiki checkout is present, use an authenticated shallow clone **outside** this repo, e.g. `git clone --depth=1 https://github.com/RovxBot/homelab-infra.wiki.git /tmp/homelab-infra-wiki`. If one exists, fetch only when needed. Do not add the Wiki as a GitOps submodule or copy all pages into this repository.
- **Documentation definition of done:** for changes that alter architecture, deployment, operations, recovery, security controls or troubleshooting, update the appropriate existing Wiki page as part of the task, after qualifying the change. Keep navigation/links correct; add a new page only when no suitable page exists. Record only observed/accepted facts, clearly label pending gates, and link to the code PR and evidence.
- **Ownership split:** code, Flux manifests, tests, CI, machine-readable evidence, immutable pins and automation-specific co-located contracts stay in `homelab-infra`; human-facing maintained documentation belongs in the Wiki. Older `README.md`/`docs/`/component runbooks can serve as historical or implementation references; **do not create a second evolving source of truth** or bulk-migrate them without a separate request.
- The Wiki does not participate in normal code PR checks. Prepare documentation alongside the implementation, but publish facts as *deployed/accepted* only after verified merge/rollout. For routine non-sensitive Wiki edits within the authorised task, commit and push a narrowly scoped change to the separate Wiki repository and report its commit/page link. For security-sensitive or architectural decisions requiring review, present a Wiki diff for approval first.
- If Wiki access or write permission is unavailable, **do not silently substitute a new `docs/` page**. Provide the exact intended Wiki page, proposed edit and access blocker at handoff. Never publish private Gitea findings, credentials, internal secrets or other confidential material in the public Wiki.

## Repository map (open only the relevant destination)

| Task | Start here |
| --- | --- |
| GitOps composition/reconciliation | `clusters/home/`, `README.md#gitops-topology` |
| Applications | `apps/<app>/`, associated `kustomization.yaml` |
| Infrastructure | `infra/<component>/` |
| Kubescape security scanning | Matching Wiki page for operations; implementation: `infra/kubescape/`, recurring: `infra/kubescape/recurring/`, DB refresh: `infra/kubescape/database-maintenance.md` |
| Kubescape tests and validation | `scripts/ci/check_kubescape.py`, `scripts/ci/check_kubescape_recurring.py`, `.github/workflows/kubescape-checks.yml` |
| Kyverno/PSA | `infra/kyverno-policies/`, `infra/kyverno/`, `migration/kyverno/` |
| Cilium | Matching Wiki page; source: `infra/cilium/` |
| Longhorn / recovery | Matching Wiki page; source: `infra/longhorn/`, `infra/longhorn-node-disks/` |
| Gatus/health | `infra/observability/` |
| Gitea and runners | Matching Wiki page; source: `ops/gitea/` and existing Gitea runbooks as needed |
| WotLK images and workloads | `apps/wotlk/`, `ops/wotlk/`, `WOTLK.md` |
| Renovate | Matching Wiki page; source: `renovate.json` |
| CI selection | `scripts/ci/detect_changes.py`, `.github/workflows/ci.yml` |
| Terraform edge | Only the affected root under `terraform/`; do not mix OCI environments |
| Accepted decisions | Relevant Wiki architecture page and any existing decision record in `docs/decisions/` |

## Non-negotiable operating boundaries

- Flux owns deployed Kubernetes desired state. Make changes through focused PRs against `main`; do not `kubectl apply/patch/delete` Flux-owned production resources as a shortcut.
- **Never change Cilium `policyEnforcementMode=never`** without a separately approved migration. Keep kube-proxy/Cilium networking and accepted LAN exposure unchanged.
- Preserve Kyverno CEL/PSA enforcement and explicit exceptions. No silent policy weakening, broad privileged permissions or automatic security remediation.
- Protect Longhorn data, healthy three-replica placements, backups and snapshots. No PVC deletion, forced rebuild, bulk snapshot removal, node draining or destructive restore without explicit approval and a proven recovery path.
- Never commit plaintext secrets, kubeconfigs, Talos configs, Terraform state or decrypted SOPS material. Do not print credentials or private findings in logs, PRs or public issues.
- No new Prometheus/Grafana/Loki stack or unrequested infrastructure. Use existing Gatus, Metrics Server, focused logs/events and read-only diagnostics.
- Keep security vulnerability findings from **private Gitea** private. Do not accidentally link unrelated upstream GitHub issues/PRs to this project's work.
- Renovate normally batches routine patches/digests for Monday; higher-risk changes are reviewed separately. No automatic merging or unrelated version churn.
- Never claim a check passed, a release is Ready or an assessment is fresh without evidence. Distinguish simulation, CI, manual canary and unattended production behaviour.

## Efficient implementation and tests

1. Scope the edit to the specified issue and smallest owning component. Prefer one coherent PR; do not opportunistically refactor other systems.
2. For a small change, make the change directly; no mandatory broad plan, exhaustive dependency audit, extra documentation or speculative compatibility matrix.
3. First run syntax, a focused unit test and the affected component's render/validator **when appropriate**. Examples for Kubescape changes:
   - `python3 -m unittest discover -s scripts/tests -p 'test_kubescape_recurring.py'`
   - `python3 -m unittest discover -s scripts/ci/tests -p 'test_kubescape.py'`
   - `python3 scripts/ci/check_kubescape_recurring.py`
   - `kustomize build infra/kubescape >/dev/null`
4. Run only the checks affected by the change. The PR's `Repo Checks` already performs change-based selection and a weekly full audit. Do not rerun full-repo renders, every unit test, exhaustive Longhorn checks or unrelated build pipelines locally by default.
5. Run expensive image builds, live acceptance, destructive tests or multi-hour integration suites only when the task explicitly needs them. Reuse CI artifacts and previously verified immutable pins.
6. If a test fails, inspect the **first relevant failure** and its exact inputs; avoid repeatedly running the same unchanged failing job.
7. Push the completed, focused PR. If CI is pending, provide the PR/check links and **stop rather than repeatedly polling**. Only continue to merge/deploy/production validation when the current task expressly authorizes it and the necessary gates are already verified.

## Handoff format (keep short)

Report only: changed paths and PR/commit, any relevant Wiki page/commit, focused checks and their outcomes, any production evidence, the **specific remaining blocker** and the next smallest action. Link to retained evidence rather than pasting entire logs, long manifests, giant JSON or repeated summaries into the conversation.

For prolonged work, leave a short issue comment with decisions, validated commit/digest, observed state and next action. Update relevant Wiki documentation when the task changes accepted operation or architecture; do not create a new runbook in the main repository by default. Do not create a new progress file for every minor edit.

**Stop rule:** When the task is implemented and focused validation is complete, or the next gate requires external CI/live evidence/human decision, hand off immediately. Do not consume a session waiting for unchanged external state.
