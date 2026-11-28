-- The official PostgreSQL entrypoint runs this only for an empty PGDATA.
-- Zabbix imports and upgrades its own bundled schema; do not duplicate it here.
\getenv zabbix_password ZABBIX_DATABASE_PASSWORD
SELECT format('CREATE ROLE zabbix LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L', :'zabbix_password') \gexec
CREATE DATABASE zabbix OWNER zabbix TEMPLATE template0 ENCODING 'UTF8'
  LC_COLLATE 'en_US.utf8' LC_CTYPE 'en_US.utf8';
