# Gatus cluster-health incident recovery

Gatus owns incident creation. The issue-sync CronJob enriches existing incidents
and provides a recovery fallback. Both paths require 20 consecutive successful
cluster-health checks; failure detection remains three failures at a one-minute
interval. Other endpoints retain the GitHub provider defaults of three failures
and two successes. The health collector and endpoint conditions are unchanged.

The cluster endpoint's `alerts` entry overrides `failure-threshold: 3`,
`success-threshold: 20` and `send-on-resolved: true`. These are alert fields,
outside `provider-override`. In pinned Gatus v5.34.0,
[default merging](https://github.com/TwiN/gatus/blob/v5.34.0/alerting/provider/provider.go)
only fills unset thresholds. The
[watchdog](https://github.com/TwiN/gatus/blob/v5.34.0/watchdog/alerting.go)
resets consecutive successes on failure and sends the normal GitHub resolved
notification at success 20. The
[GitHub provider](https://github.com/TwiN/gatus/blob/v5.34.0/alerting/provider/github/github.go)
closes matching open issues from the token's creator without editing their body.

The helper has additional evidence checks, independently configured through its
CronJob environment:

| Variable | Default | Meaning |
| --- | --- | --- |
| `CLUSTER_SUCCESS_THRESHOLD` | `20` | Latest consecutive successful samples |
| `CLUSTER_RECOVERY_MIN_SECONDS` | `1140` | Minimum first-to-last span: 19 minutes |
| `CLUSTER_MAX_SAMPLE_GAP_SECONDS` | `90` | Maximum adjacent gap, allowing scheduler jitter |
| `CLUSTER_MAX_RESULT_AGE_SECONDS` | `120` | Maximum age of the newest Gatus result |
| `CLUSTER_MAX_HEALTH_AGE_SECONDS` | `300` | Maximum age of direct `health.json` observation |

History must have valid, distinct, timezone-aware timestamps in strict ascending
or descending order. Shuffled or future-dated history is rejected. All of the
latest 20 samples must explicitly succeed, cover the minimum duration and meet
the gap bound. A missing one-minute sample produces a two-minute gap and blocks
closure. Older historical samples are allowed beyond the non-cluster global
900-second freshness window; only the newest observation must be fresh.

Direct collector evidence must be fresh, have overall status `healthy`, and
explicitly report `ok` for nodes, workloads, PVCs, Flux, Longhorn and recent Jobs.
Any reported failure identity or collection error contradicts recovery. Missing,
invalid, unknown or unavailable evidence cannot close an incident. Invalid
cluster configuration fails closed independently of non-cluster recoveries:
counts below 20, durations below 1140, nonpositive or noninteger settings,
gaps outside 60–119 seconds, and impossible count/duration/gap combinations are
rejected. Stronger settings must remain compatible with retained history. Change
the native success threshold too when deliberately increasing the recovery count.

The helper retains creator-filtered selection, exact titles and labels, and only
updates its managed diagnostic block. During valid but incomplete recovery it
leaves the last failure diagnostics and user text intact. It never creates an
incident. With its ten-minute schedule it can close an eligible incident later
than Gatus; it cannot establish recovery from two or ten successes.

## Storage and version limitations

The existing `/data/data.db` SQLite database needs no schema or retention change.
Gatus v5.34.0 defaults to
[100 retained results](https://github.com/TwiN/gatus/blob/v5.34.0/storage/config.go);
the [status handler](https://github.com/TwiN/gatus/blob/v5.34.0/api/endpoint_status.go)
requests this history and the
[SQL adapter](https://github.com/TwiN/gatus/blob/v5.34.0/storage/store/sql/sql.go)
returns it chronologically. Twenty one-minute samples fit. Insufficient history
after startup keeps the helper from closing anything.

The native watchdog counts checks; it does not apply the helper's wall-clock gap
or direct collector gates. The unchanged collector supplies the native healthy
condition. SQLite preserves a partially recovered triggered alert across an
ordinary restart with the same configuration. However, changing the success
threshold changes the upstream
[alert checksum](https://github.com/TwiN/gatus/blob/v5.34.0/alerting/alert/alert.go).
Gatus does not restore the old triggered state for that changed configuration,
and its GitHub creation path does not deduplicate existing open issues. The first
rollout can therefore create another issue after three failures while an old
incident remains open. This change prevents subsequent short-success flapping;
it does not migrate upstream alert state or force-close existing incidents.

Kyverno webhook timeouts remain tracked in
[#2133](https://github.com/RovxBot/homelab-infra/issues/2133). Conservative recovery
does not fix that dependency or weaken cluster health conditions.

## Offline regression validation

Run the Python suites and normal repository manifest/policy checks. The helper
image check also runs `test_gatus_issue_sync.py` using synthetic APIs:

```sh
python3 -m unittest discover -s scripts/tests -v
python3 -m unittest discover -s scripts/ci/tests -v
python3 -m unittest discover -s ops/gitea/tests -v
python3 scripts/ci/check_python_helpers.py
```

The native integration fixture runs the actual pinned configuration parser,
GitHub provider, watchdog and SQLite storage. It uses an HTTP server on loopback
and a temporary database; it never starts monitoring or calls production APIs.
With Helm, Go and PyYAML installed, run from the repository root:

```sh
task_dir=$(mktemp -d)
export TASK_GATUS_DIR="$task_dir"
python3 - <<'PY'
import os
from pathlib import Path
import yaml
values = yaml.safe_load(Path('infra/observability/gatus-helmrelease.yaml').read_text())['spec']['values']
Path(os.environ['TASK_GATUS_DIR'], 'values.yaml').write_text(yaml.safe_dump(values))
PY
helm template gatus gatus --repo https://twin.github.io/helm-charts \
  --version 1.5.0 --namespace monitoring -f "$task_dir/values.yaml" > "$task_dir/rendered.yaml"
python3 - <<'PY'
import os
from pathlib import Path
import yaml
root = Path(os.environ['TASK_GATUS_DIR'])
for document in yaml.safe_load_all((root / 'rendered.yaml').read_text()):
    if document and document['kind'] == 'ConfigMap' and 'config.yaml' in document.get('data', {}):
        (root / 'config.yaml').write_text(document['data']['config.yaml'])
PY
git clone --depth 1 --branch v5.34.0 https://github.com/TwiN/gatus.git "$task_dir/gatus"
test "$(git -C "$task_dir/gatus" rev-parse HEAD)" = 1c532971cbac4bc243c6a82fd641ab58b1199993
cp scripts/tests/fixtures/gatus_recovery_test.go "$task_dir/gatus/homelab_recovery_test.go"
(cd "$task_dir/gatus" && HOMELAB_GATUS_CONFIG="$task_dir/config.yaml" \
  go test -run '^TestHomelabRecovery$' -v .)
```

The fixture verifies creation at failure three, no resolution through success
19 (including two and ten), failure resetting recovery, resolution at success
20, unchanged effective thresholds for every other endpoint, and sufficient
SQLite history across a restart.

## Review and release handoff

The repository owner reviews this focused PR and its CI before any merge.
Deployment remains a separate decision: Flux would reconcile the HelmRelease and
helper ConfigMap/CronJob after an approved merge. Verify effective thresholds and
fresh endpoint/collector evidence after that deployment. Failed or unknown checks
must keep incidents active; do not manufacture acceptance by changing issue state.
Revert through a reviewed GitOps change if needed, recognizing that reverting
these thresholds restores the former premature recovery risk. This task stops
at PR review and performs no deployment or incident-state writes.

## Additional active incident diagnostics

The helper enriches existing creator-owned backup and Koito bridge incidents with
named Deployment readiness, bounded Pod/event categories and the existing Flux
scope. Backup success still requires the monitored protection endpoint; a Ready
Deployment is not backup proof. CVE reporting and existing policy assurance
incidents use only the exact allowlisted health field and a fresh timestamp from
`security-reporting-health.json`; no private report, exception identity, raw Job
log, credential or finding data is queried or published. Both security endpoints
share one read per helper invocation. The retired public policy-assurance alert
creation route stays disabled; enrichment of an existing issue is not a new
writer or a change to recovery criteria. All original recovery thresholds remain.
