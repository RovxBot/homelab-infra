#!/usr/bin/env bash
set -euo pipefail

mode="${WOW_MODE:-}"

if [[ -z "${mode}" ]]; then
  echo "WOW_MODE must be set to one of: realmd, mangosd" >&2
  exit 2
fi

db_host="${DB_HOST:-wow-mariadb.wow.svc.cluster.local}"
db_port="${DB_PORT:-3306}"
db_user="${DB_USER:-wow}"
db_pass="${DB_PASSWORD:-}"

if [[ -z "${db_pass}" ]]; then
  echo "DB_PASSWORD must be set" >&2
  exit 2
fi

realm_db="${DB_REALM_DB:-realmd}"
world_db="${DB_WORLD_DB:-mangos0}"
char_db="${DB_CHAR_DB:-character0}"

realm_port="${REALM_PORT:-3724}"
world_port="${WORLD_PORT:-8085}"
bind_ip="${BIND_IP:-0.0.0.0}"

data_dir="${DATA_DIR:-/data}"
mkdir -p /config /data

if [[ "${mode}" == "realmd" ]]; then
  cat > /config/realmd.conf <<EOF
[RealmdConf]
ConfVersion=1
LoginDatabaseInfo      = "${db_host};${db_port};${db_user};${db_pass};${realm_db}"
LogsDir                = ""
PidFile                = ""
MaxPingTime            = 30
RealmServerPort        = ${realm_port}
BindIP                 = "${bind_ip}"
LogLevel               = 1
LogTime                = 1
LogFile                = ""
LogTimestamp           = 0
LogFileLevel           = 0
LogColors              = ""
RealmsStateUpdateDelay = 20
EOF
  exec /opt/mangos/bin/realmd -c /config/realmd.conf
fi

if [[ "${mode}" == "mangosd" ]]; then
  cat > /config/mangosd.conf <<EOF
[MangosdConf]
ConfVersion=1
RealmID                      = 1
DataDir                      = "${data_dir}"
LogsDir                      = ""
LoginDatabaseInfo            = "${db_host};${db_port};${db_user};${db_pass};${realm_db}"
WorldDatabaseInfo            = "${db_host};${db_port};${db_user};${db_pass};${world_db}"
CharacterDatabaseInfo        = "${db_host};${db_port};${db_user};${db_pass};${char_db}"
LoginDatabaseConnections     = 1
WorldDatabaseConnections     = 2
CharacterDatabaseConnections = 1
MaxPingTime                  = 5
WorldServerPort              = ${world_port}
BindIP                       = "${bind_ip}"
LogLevel                     = 1
LogTime                      = 1
LogFile                      = ""
EOF
  exec /opt/mangos/bin/mangosd -c /config/mangosd.conf
fi

echo "Unknown WOW_MODE=${mode}" >&2
exit 2

