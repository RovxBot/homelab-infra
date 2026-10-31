#!/usr/bin/env bash
# Runs in a disposable Job on a NEW PVC. Never mount production data here.
set -euo pipefail
umask 077
export RESTIC_CACHE_DIR=/restore/cache
test ! -e /restore/restored || { echo 'Use an empty restore-test PVC'; exit 1; }
restic snapshots --host cooked-gitea --tag gitea-cold --latest 1 --json > /restore/snapshot.json
restic restore latest --host cooked-gitea --tag gitea-cold \
  --target /restore/restored --verify
dump=/restore/restored/work/export/gitea.pgdump
expected=$(awk '{print $1}' /restore/restored/work/export/SHA256SUMS)
echo "$expected  $dump" | sha256sum --check -

mkdir -p /restore/socket
initdb --pgdata=/restore/database --username=restore_test --auth=trust >/restore/initdb.log
pg_ctl --pgdata=/restore/database --log=/restore/postgres.log \
  --options='-k /restore/socket -p 55432 -c listen_addresses=127.0.0.1' --wait start
trap 'pg_ctl --pgdata=/restore/database --mode=fast --wait stop' EXIT
export PGHOST=/restore/socket PGPORT=55432 PGUSER=restore_test
psql --dbname=postgres --set=ON_ERROR_STOP=1 --file=/restore/restored/work/export/globals.sql
createdb --owner=gitea gitea
pg_restore --dbname=gitea --exit-on-error "$dump"
psql --dbname=gitea --set=ON_ERROR_STOP=1 --tuples-only --no-align \
  --command="SELECT json_build_object('repositories', (SELECT count(*) FROM repository), 'issues', (SELECT count(*) FROM issue), 'comments', (SELECT count(*) FROM comment), 'artifacts', (SELECT count(*) FROM action_artifact), 'app_is_superuser', (SELECT rolsuper FROM pg_roles WHERE rolname='gitea'));" \
  > /restore/database-evidence.json
psql --dbname=gitea --set=ON_ERROR_STOP=1 --tuples-only --no-align \
  --command="SELECT COALESCE(json_agg(json_build_object('id', id, 'path', storage_path, 'size', file_compressed_size)), '[]'::json) FROM action_artifact WHERE status = 2;" \
  > /restore/artifact-evidence.json
touch /restore/database-restored
