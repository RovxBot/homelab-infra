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
  exit
fi

if [[ "${1:-}" == init ]]; then
  # Only use on the dedicated, new repository after checking the destination.
  restic init
  restic check
  exit
fi

stopped=()
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
    if ! kubectl --request-timeout=15s -n gitea scale "deployment/$deployment" --replicas=1; then
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
  resume_services || result=1
  if (( result != 0 )); then
    date -u +%s > /protection/backup-failure.partial
    mv /protection/backup-failure.partial /protection/backup-failure.epoch
  fi
  exit "$result"
}
trap restore_services EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

# Do not take the service down when the backup destination is inaccessible.
run_command timeout --kill-after=10s 2m restic snapshots --json > /work/export/previous-snapshots.json
available_kib=$(df -Pk /work | awk 'NR == 2 {print $4}')
(( available_kib > 1048576 )) || { echo 'Insufficient backup staging space'; exit 1; }

for deployment in gitea-mirror gitea-protection gitea; do
  replicas=$(kubectl --request-timeout=15s -n gitea get "deployment/$deployment" -o jsonpath='{.spec.replicas}')
  # A retired mirror console remains stopped across cold backups.
  if [[ "$deployment" == gitea-mirror && "$replicas" == 0 ]]; then
    continue
  fi
  [[ "$replicas" == 1 ]] || { echo "Expected one replica of $deployment; maintenance may be in progress"; exit 1; }
  stopped+=("$deployment")
  run_command kubectl --request-timeout=15s -n gitea scale "deployment/$deployment" --current-replicas=1 --replicas=0
done
for selector in 'app=gitea-mirror' 'app=gitea-protection' 'app.kubernetes.io/name=gitea,app.kubernetes.io/instance=gitea'; do
  pod_names=$(kubectl --request-timeout=15s -n gitea get pods -l "$selector" -o name)
  if [[ -n "$pod_names" ]]; then
    mapfile -t pods <<< "$pod_names"
    run_command kubectl --request-timeout=190s -n gitea wait --for=delete "${pods[@]}" --timeout=180s
  fi
done

echo 'Starting cold capture (maximum 20 minutes)'
run_command timeout --signal=TERM --kill-after=10s "${COLD_BACKUP_TIMEOUT:-20m}" "$BASH" "$0" capture
# Checking the remote repository and applying retention do not need downtime.
resume_services
echo 'Services resumed; checking backup integrity and retention'
run_command timeout --kill-after=10s 15m restic check
run_command timeout --kill-after=10s 2m restic forget --host cooked-gitea --tag gitea-cold \
  --keep-daily 14 --keep-weekly 8 --keep-monthly 12
# Pruning is an explicit maintenance operation; retention never touches the
# Longhorn or Immich repositories. A failed backup never runs forget.
date -u +%s > /protection/backup-success.partial
mv /protection/backup-success.partial /protection/backup-success.epoch
rm -f /protection/backup-failure.epoch
echo 'Cold backup and repository metadata integrity check completed'
