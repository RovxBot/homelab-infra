#!/usr/bin/env bash
# Cold application backup: database plus repositories, LFS, artifacts and mirror DB.
set -euo pipefail
umask 077
export RESTIC_CACHE_DIR=/work/cache
export PGPASSWORD="$DATABASE_ADMIN_PASSWORD"
export PGHOST=gitea-postgres PGUSER=gitea_admin PGDATABASE=gitea
mkdir -p /work/export

# Run the cold capture as a separate, bounded process group. The supervising
# shell waits asynchronously so Kubernetes SIGTERM can run its cleanup trap
# immediately, even while pg_dump or restic is blocked on I/O.
if [[ "${1:-}" == capture ]]; then
  pg_dump --format=custom --file=/work/export/gitea.pgdump
  cp /scripts/protected-repositories.json /work/export/protected-repositories.json
  pg_restore --list /work/export/gitea.pgdump > /work/export/database-contents.txt
  pg_dumpall --globals-only --no-role-passwords > /work/export/globals.sql
  sha256sum /work/export/gitea.pgdump > /work/export/SHA256SUMS
  date -u +%FT%TZ > /work/export/captured-at
  restic backup /gitea /protection /mirror /recovery /work/export \
    --host cooked-gitea --tag gitea-cold --json > /work/backup-result.json
  restic check
  restic forget --host cooked-gitea --tag gitea-cold \
    --keep-daily 14 --keep-weekly 8 --keep-monthly 12
  # Pruning is an explicit maintenance operation. A failed capture or check
  # never runs forget; this repository is separate from Longhorn and Immich.
  exit
fi

if [[ "${1:-}" == init ]]; then
  # Only use on the dedicated, new repository after checking the destination.
  restic init
  restic check
  exit
fi

stopped=()
lock_held=false
kube=(kubectl --cache-dir=/work/kubectl-cache)
command_pid=
run_command() {
  local result
  "$@" &
  command_pid=$!
  if wait "$command_pid"; then result=0; else result=$?; fi
  command_pid=
  return "$result"
}

resume_services() {
  local deployment i
  local pending=()
  # Start Gitea before its dependent services; never start the retired mirror.
  for ((i=${#stopped[@]}-1; i>=0; i--)); do
    deployment=${stopped[i]}
    if ! timeout --kill-after=2s 12s "${kube[@]}" --request-timeout=10s -n gitea scale "deployment/$deployment" --replicas=1; then
      pending+=("$deployment")
    fi
  done
  stopped=("${pending[@]}")
  (( ${#stopped[@]} == 0 ))
}

restore_services() {
  local result=$?
  trap - EXIT TERM INT
  if [[ -n "$command_pid" ]]; then
    # GNU timeout forwards TERM to the capture process group, then enforces
    # --kill-after. Stop the writer before allowing the application to write.
    kill -TERM "$command_pid" 2>/dev/null || true
    wait "$command_pid" 2>/dev/null || true
  fi
  # Two bounded cleanup attempts fit within the 90-second termination grace
  # period, including termination of the capture process group. A failed
  # restoration attempt always leaves this run failed, even if a retry works.
  if ! resume_services; then
    result=1
    resume_services || echo "ERROR: service restoration incomplete: ${stopped[*]}" >&2
  fi
  rm -f /work/kubeconfig
  if "$lock_held"; then rmdir /protection/backup.lock || result=1; fi
  if (( result != 0 )); then
    date -u +%s > /protection/backup-failure.partial
    mv /protection/backup-failure.partial /protection/backup-failure.epoch
  fi
  exit "$result"
}
trap restore_services EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

# enableServiceLinks is disabled on the Job. Do not rely on injected service
# environment variables or kubectl's localhost fallback. tokenFile follows the
# projected token's rotation without copying credentials into argv or the PVC.
sa_dir=${SERVICEACCOUNT_DIR:-/var/run/secrets/kubernetes.io/serviceaccount}
[[ -s "$sa_dir/token" && -r "$sa_dir/token" ]] || { echo 'Missing ServiceAccount token' >&2; exit 1; }
[[ -s "$sa_dir/ca.crt" && -r "$sa_dir/ca.crt" ]] || { echo 'Missing cluster CA' >&2; exit 1; }
export KUBECONFIG=/work/kubeconfig
[[ "${KUBERNETES_API_SERVER:-https://kubernetes.default.svc:443}" == https://* ]] || { echo 'Kubernetes API requires HTTPS' >&2; exit 1; }
cat > "$KUBECONFIG" <<EOF
apiVersion: v1
kind: Config
clusters:
- name: in-cluster
  cluster:
    server: ${KUBERNETES_API_SERVER:-https://kubernetes.default.svc:443}
    certificate-authority: $sa_dir/ca.crt
users:
- name: backup
  user:
    tokenFile: $sa_dir/token
contexts:
- name: backup
  context:
    cluster: in-cluster
    user: backup
    namespace: gitea
current-context: backup
EOF

# A shared, credential-free lock also covers manually created backup Jobs.
# A stale lock after node loss must be inspected and removed by an operator.
[[ ! -e /protection/maintenance.lock ]] || { echo 'Maintenance lock present' >&2; exit 1; }
mkdir /protection/backup.lock || { echo 'Another backup or stale backup lock exists' >&2; exit 1; }
lock_held=true

# Complete ALL checks before the first scale request. A GET proves TLS, token
# authentication, connectivity and deployment read access, including the retired
# mirror. Authorization checks cover both stop and restore (same scale verbs).
for deployment in gitea-mirror gitea-protection gitea; do
  replicas=$("${kube[@]}" --request-timeout=10s -n gitea get "deployment/$deployment" -o jsonpath='{.spec.replicas}')
  expected=1
  [[ "$deployment" != gitea-mirror ]] || expected=0
  [[ "$replicas" == "$expected" ]] || { echo "Unexpected replica count for $deployment; maintenance may be in progress" >&2; exit 1; }
  for verb in get patch update; do
    "${kube[@]}" --request-timeout=10s -n gitea auth can-i "$verb" "deployments/$deployment" --subresource=scale --quiet
  done
done
for verb in list watch; do
  "${kube[@]}" --request-timeout=10s -n gitea auth can-i "$verb" pods --quiet
done
active_jobs=$("${kube[@]}" --request-timeout=10s -n gitea get jobs -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{.status.active}{"\n"}{end}')
while IFS='|' read -r job active; do
  if [[ "${active:-0}" != 0 && "$job" != "${BACKUP_JOB_NAME:-}" ]]; then
    echo "Active Job $job; refusing overlapping maintenance" >&2
    exit 1
  fi
done <<< "$active_jobs"

run_command timeout --kill-after=10s 2m restic snapshots --json > /work/export/previous-snapshots.json
run_command timeout --kill-after=10s 2m restic list locks --json > /work/restic-locks.json
[[ ! -s /work/restic-locks.json ]] || { echo 'Restic repository is locked' >&2; exit 1; }
# df alone sees node capacity rather than emptyDir.sizeLimit. Budget for both
# the volume limit and the database export, with room for metadata and cache.
available_kib=$(df -Pk /work | awk 'NR == 2 {print $4}')
used_kib=$(du -sk /work | awk '{print $1}')
volume_free_kib=$((3145728 - used_kib))
(( available_kib <= volume_free_kib )) || available_kib=$volume_free_kib
database_bytes=$(psql -Atqc 'SELECT pg_database_size(current_database())')
[[ "$database_bytes" =~ ^[0-9]+$ ]] || { echo 'Cannot size database export' >&2; exit 1; }
required_kib=$((database_bytes / 512 + 131072))
(( required_kib >= 1048576 )) || required_kib=1048576
(( available_kib > required_kib )) || { echo 'Insufficient backup staging space' >&2; exit 1; }

for deployment in gitea-protection gitea; do
  # Track intent BEFORE submitting: the API may commit a scale request whose
  # response is lost. Cleanup must recover that ambiguous partial shutdown.
  stopped+=("$deployment")
  run_command timeout --kill-after=2s 12s "${kube[@]}" --request-timeout=10s -n gitea scale "deployment/$deployment" --current-replicas=1 --replicas=0
done
for selector in 'app=gitea-mirror' 'app=gitea-protection' 'app.kubernetes.io/name=gitea,app.kubernetes.io/instance=gitea'; do
  pod_names=$("${kube[@]}" --request-timeout=10s -n gitea get pods -l "$selector" -o name)
  if [[ -n "$pod_names" ]]; then
    mapfile -t pods <<< "$pod_names"
    run_command "${kube[@]}" --request-timeout=190s -n gitea wait --for=delete "${pods[@]}" --timeout=180s
  fi
done

echo 'Starting cold capture and integrity verification (maximum 20 minutes)'
run_command timeout --signal=TERM --kill-after=10s "${COLD_BACKUP_TIMEOUT:-20m}" "$BASH" "$0" capture
# This Pod holds the RWO volumes until it exits. Keep verification inside the
# same timeout so rescheduled application Pods can acquire them promptly.
resume_services
date -u +%s > /protection/backup-success.partial
mv /protection/backup-success.partial /protection/backup-success.epoch
rm -f /protection/backup-failure.epoch
echo 'Cold backup verified; application replicas restored and backup Pod exiting to release volumes'
