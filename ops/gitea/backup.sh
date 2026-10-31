#!/usr/bin/env bash
# Cold application backup: database plus repositories, LFS, artifacts and mirror DB.
set -euo pipefail
umask 077
export RESTIC_CACHE_DIR=/work/cache
export PGPASSWORD="$DATABASE_ADMIN_PASSWORD"
export PGHOST=gitea-postgres PGUSER=gitea_admin PGDATABASE=gitea
mkdir -p /work/export

if [[ "${1:-}" == init ]]; then
  # Only use on the dedicated, new repository after checking the destination.
  restic init
  restic check
  exit
fi

stopped=()
restore_services() {
  result=$?
  trap - EXIT TERM INT
  for deployment in "${stopped[@]}"; do
    kubectl -n gitea scale "deployment/$deployment" --replicas=1 || result=1
  done
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
restic snapshots --json > /work/export/previous-snapshots.json
available_kib=$(df -Pk /work | awk 'NR == 2 {print $4}')
(( available_kib > 1048576 )) || { echo 'Insufficient backup staging space'; exit 1; }

for deployment in gitea-mirror gitea-protection gitea; do
  replicas=$(kubectl -n gitea get "deployment/$deployment" -o jsonpath='{.spec.replicas}')
  [[ "$replicas" == 1 ]] || { echo "Expected one replica of $deployment; maintenance may be in progress"; exit 1; }
  stopped+=("$deployment")
  kubectl -n gitea scale "deployment/$deployment" --current-replicas=1 --replicas=0
done
for selector in 'app=gitea-mirror' 'app=gitea-protection' 'app.kubernetes.io/name=gitea,app.kubernetes.io/instance=gitea'; do
  mapfile -t pods < <(kubectl -n gitea get pods -l "$selector" -o name)
  if (( ${#pods[@]} )); then
    kubectl -n gitea wait --for=delete "${pods[@]}" --timeout=180s
  fi
done

pg_dump --format=custom --file=/work/export/gitea.pgdump
pg_restore --list /work/export/gitea.pgdump > /work/export/database-contents.txt
pg_dumpall --globals-only --no-role-passwords > /work/export/globals.sql
sha256sum /work/export/gitea.pgdump > /work/export/SHA256SUMS
date -u +%FT%TZ > /work/export/captured-at

restic backup /gitea /protection /mirror /recovery /work/export \
  --host cooked-gitea --tag gitea-cold --json > /work/backup-result.json
restic check
restic forget --host cooked-gitea --tag gitea-cold \
  --keep-daily 14 --keep-weekly 8 --keep-monthly 12
# Pruning is an explicit maintenance operation; retention never touches the
# Longhorn or Immich repositories. A failed backup never runs forget.
date -u +%s > /protection/backup-success.partial
mv /protection/backup-success.partial /protection/backup-success.epoch
rm -f /protection/backup-failure.epoch
echo 'Cold backup and repository metadata integrity check completed'
