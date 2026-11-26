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
  transport stage healthy. Remove legacy Deployment, Service, ServiceAccount,
  named RBAC, generated config, PVC from desired state and the two non-alerting
  Gatus checks. Preserve active security state, runtime bindings and all issues.
- Operator explicitly chose permanent deletion of obsolete collector data
  without a dedicated backup. Its old claim is prune-protected, so Flux can
  remove the workload and permissions but leave the PVC behind.
- After the exact retirement revision is Ready and no Pod references the old
  claim, delete only `kubescape/security-collector-state`. The previously
  inspected PV `pvc-28c469e0-c283-4576-ad49-1e8dc0c9384b` has reclaim policy
  Delete. Check the claim still names that PV before deletion; never delete
  a different claim or the receiver claim. Verify the old PV and Longhorn
  volume disappear and native receiver/publisher claims remain Bound.
- This is irreversible for historical collector data. No restoration of
  legacy state is promised. Historical source files do not deploy resources;
  they can be removed from the active repository separately.
- Verify native receiver/agents Ready, independent native stage successful,
  no unintended incident closure and no legacy deployment, RBAC, volume
  or diagnostics remaining. Leave active state and normal backups intact.

## Completion gates

| Gate | Current evidence / action |
| --- | --- |
| Admission prevention | CEL and PSA active; retain reviewed exceptions and independent assurance |
| Runtime transport | Genuine canary and restart recorded; eight agents deployed, lossless coverage unverified |
| Fixture retirement | Verified removed on 26 November; native receiver and eight agents Ready |
| CVE assessment | Last observed refresh 9/12 healthy, 7/12 complete projections; #2281–#2283 need live qualification |
| Private reporting | Native stage successful; CVE stage reports incomplete coverage until qualified cohort complete |
| Candidate guidance | Implemented, optional token not configured at last observed run |
| Recovery | Shadow proof/state machines implemented; require controlled live positive/replacement/recurrence acceptance; automatic state transitions disabled |
| Additional scope | Sidecar/cache draft must be rebased and qualified only after current cohort acceptance |
| Legacy retirement | #2286 removes all legacy resources from desired state; verify rollout and explicitly delete old prune-protected claim |
| CI remediation and posture | Broader coverage and risk-based policy dispositions still require explicit review |

Do not close the overall project or child issues on a merge alone. Record
verified live evidence, keep specific unresolved acceptance and scope limitations,
and retain manual disposition for vulnerabilities lacking a qualified replacement.


## Existing backup coverage

The receiver and the incident, policy and admission publisher state claims are
Bound and provisioned with three Longhorn replicas. None had a recorded last
remote backup at the 26 November inspection; the three publisher volumes were
detached between Jobs, so current replica health was not independently qualified.
The existing Sunday 04:15 UTC SystemBackup uses `volume-backup-policy: always`.
Verify completed remote backups and exact original volume identities after its
next normal run, using the [existing Longhorn runbook](../../docs/runbooks/longhorn-volume-backups.md).
No special backup manifest, new schedule or change to retention. No dedicated legacy backup; remove its obsolete claim before the next normal backup where practical. No remote backup or
restore acceptance is claimed here.
