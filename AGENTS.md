# Codex guide — RovxBot/homelab-infra

This is a **short route map**, not a checklist of files to read. The repository is a personal Talos/Flux GitOps homelab, not a generic Kubernetes template. Read **only** the files relevant to the current task. A user-supplied issue, PR, path or failure is the starting point.

## Find the relevant code without rereading the repository

- Start with `git status --short` and `git diff --stat` to avoid overwriting work. Identify the requested paths from the task/issue first.
- Narrow searches: `rg --files infra/kubescape`, `rg -n 'symbol' scripts/ci infra/kubescape`. Read specific files or line ranges, not every manifest, README or historical report.
- Do not run a full repository tree dump, recursive `cat`, blanket `find` over all content, or a complete source/history audit unless the task requires it.
- Existing docs/evidence are authoritative for *their recorded state*; check changed configuration or live state only when relevant. Never rediscover a validated design decision without a concrete inconsistency.
- Keep research bounded: a short hypothesis and the smallest confirming test beat repeated speculative explorations.

## Repository map (open only the relevant destination)

| Task | Start here |
| --- | --- |
| GitOps composition/reconciliation | `clusters/home/`, `README.md#gitops-topology` |
| Applications | `apps/<app>/`, associated `kustomization.yaml` |
| Infrastructure | `infra/<component>/` |
| Kubescape security scanning | `infra/kubescape/README.md`; recurring: `infra/kubescape/recurring/README.md`; DB refresh: `infra/kubescape/database-maintenance.md` |
| Kubescape tests and validation | `scripts/ci/check_kubescape.py`, `scripts/ci/check_kubescape_recurring.py`, `.github/workflows/kubescape-checks.yml` |
| Kyverno/PSA | `infra/kyverno-policies/`, `infra/kyverno/`, `migration/kyverno/` |
| Cilium | `infra/cilium/`, `docs/runbooks/cilium-primary-operations.md` |
| Longhorn / recovery | `infra/longhorn/`, `infra/longhorn-node-disks/`, relevant `docs/runbooks/` |
| Gatus/health | `infra/observability/` |
| Gitea and runners | `ops/gitea/`, `docs/gitea/` |
| WotLK images and workloads | `apps/wotlk/`, `ops/wotlk/`, `WOTLK.md` |
| Renovate | `renovate.json`, `docs/runbooks/renovate-operations.md` |
| CI selection | `scripts/ci/detect_changes.py`, `.github/workflows/ci.yml` |
| Terraform edge | Only the affected root under `terraform/`; do not mix OCI environments |
| Accepted decisions | The matching entry in `docs/decisions/` |

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

Report only: changed paths and PR/commit, focused checks and their outcomes, any production evidence, the **specific remaining blocker** and the next smallest action. Link to retained evidence rather than pasting entire logs, long manifests, giant JSON or repeated summaries into the conversation.

For prolonged work, leave a short issue comment or scoped runbook with decisions, validated commit/digest, observed state and next action so the next session can resume without reconstructing the investigation. Do not create a new progress file for every minor edit.

**Stop rule:** When the task is implemented and focused validation is complete, or the next gate requires external CI/live evidence/human decision, hand off immediately. Do not consume a session waiting for unchanged external state.
