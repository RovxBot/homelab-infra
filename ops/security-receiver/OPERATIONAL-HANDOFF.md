# Security operations handoff

Current desired state: after #2284, 26 November 2026. This is a review checklist,
not a claim that every operational acceptance gate has passed.

## Activate read-only remediation correlation

1. Create a dedicated fine-grained GitHub token restricted to
   `RovxBot/homelab-infra`, repository Actions: Read and the mandatory Metadata:
   Read. No write permissions, private Gitea credentials or issue-writer reuse.
2. From a trusted local checkout with SOPS and the existing age configuration,
   create an encrypted manifest without displaying the plaintext token:

   ```bash
   set +x
   umask 077
   read -rsp 'GitHub Actions read token: ' REMEDIATION_TOKEN
   printf '\n'
   printf '%s' "$REMEDIATION_TOKEN" |
     kubectl -n monitoring create secret generic security-remediation-github-read \
       --from-file=token=/dev/stdin --dry-run=client -o yaml |
     sops --encrypt --filename-override secrets/security-remediation-github-read.enc.yaml \
       --input-type yaml --output-type yaml /dev/stdin \
       > secrets/security-remediation-github-read.enc.yaml
   unset REMEDIATION_TOKEN
   ```

   Use a Bash shell with pipefail; if any command fails, discard the incomplete
   encrypted output. Verify the manifest contains only SOPS-encrypted data
   and correct name/namespace/key without decrypting it to a terminal.
   Never put the token in an issue, chat or Git commit.
3. In the same reviewed PR, add
   `../../../secrets/security-remediation-github-read.enc.yaml` to
   `clusters/home/infrastructure/kustomization.yaml`, whose Flux owner already
   decrypts with SOPS. Do not add it to the Kubescape-only TLS secret list.
   Merge after CI and verify the infrastructure Flux revision and next
   publisher Job. Check that the scheduled discovery workflow produced a
   fresh validated candidate artifact and the publisher logs
   `remediationCorrelation: observed`. Unconfigured/unavailable/conflict is
   not acceptance. Zero matching candidates can be legitimate.
4. Candidate images are unassessed. A subsequent direct replacement assessment,
   not a PR link, must establish remediation. Rotate/revoke the token through
   a reviewed Secret update; preserve writer isolation.

## Retire legacy collection (separate reviewed PR)

- Confirm #2284 applied and Ready on Kubescape, no disposable workload or
  temporary collector RBAC, restricted test namespace retained.
- Confirm native receiver Ready, all eight agents Ready, native publisher
  transport stage healthy, both evidence claims Bound with original identities.
  Verify read-only database integrity and retained evidence; obtain and verify
  a recovery point for each evidence claim before removal. Do not inspect
  or copy raw findings to public logs.
- Retain the exact legacy PVC as a managed, prune-protected resource, and
  keep historical collector manifests and source offline for rollback.
- Remove only the legacy collector Deployment, Service, ServiceAccount,
  named roles/bindings and generated ConfigMap from active GitOps. Remove only
  its two non-alerting Gatus diagnostics. Preserve native health endpoints,
  assessment and policy publishers, runtime bindings and all existing issues.
  Update exact CI render/PSA/resource tests for retirement.
- After merge verify absence of the old Deployment/Service/RBAC, both PVCs
  still Bound, native receiver/agents Ready, independent native stage
  successful, and no unintended alert or incident closure. Revert the scoped
  PR to restore old configuration if needed; old pinned agent names may be
  stale and must be reviewed before re-enrollment.

## Completion gates

| Gate | Current evidence / action |
| --- | --- |
| Admission prevention | CEL and PSA active; retain reviewed exceptions and independent assurance |
| Runtime transport | Genuine canary and restart recorded; eight agents deployed, lossless coverage unverified |
| Fixture retirement | Desired state merged; actual removal and native health must be verified |
| CVE assessment | Last observed refresh 9/12 healthy, 7/12 complete projections; #2281–#2283 need live qualification |
| Private reporting | Native stage successful; CVE stage reports incomplete coverage until qualified cohort complete |
| Candidate guidance | Implemented, optional token not configured at last observed run |
| Recovery | Shadow proof/state machines implemented; require controlled live positive/replacement/recurrence acceptance; automatic state transitions disabled |
| Additional scope | Sidecar/cache draft must be rebased and qualified only after current cohort acceptance |
| Legacy retirement | Pending separate PR and evidence preservation checks |
| CI remediation and posture | Broader coverage and risk-based policy dispositions still require explicit review |

Do not close the overall project or child issues on a merge alone. Record
verified live evidence, keep specific unresolved acceptance and scope limitations,
and retain manual disposition for vulnerabilities lacking a qualified replacement.
