Repo expectations for `wow-db-sync`

This stack bootstraps the base MaNGOS Zero DB schema/data from upstream repos:

- World + Characters: `https://github.com/mangoszero/database`
- Realms/Auth: `https://github.com/mangos/Realm_DB`

Your custom DB repo (default `https://github.com/RovxBot/1.12.1-database`) is expected to contain:

- `migrations/` (optional): ordered `*.sql` files (e.g. `0001_gate_sm.sql`) that are safe to apply multiple times

You can change refs/paths via env vars in `apps/wow/db-migrate-cronjob.yaml`.
