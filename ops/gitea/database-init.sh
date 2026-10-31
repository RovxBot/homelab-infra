#!/usr/bin/env bash
# Executed only for an empty PostgreSQL volume. Existing clusters use the
# documented role migration; changing POSTGRES_USER does not alter old data.
set -euo pipefail
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 --set=app_password="$GITEA_DATABASE_PASSWORD" <<'SQL'
CREATE ROLE gitea LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_password';
ALTER DATABASE gitea OWNER TO gitea;
SQL
