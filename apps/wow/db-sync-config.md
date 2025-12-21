Repo expectations for `wow-db-sync`

This stack expects your DB repo (default `https://github.com/RovxBot/1.12.1-database`) to contain:

- `bootstrap/` (optional): one or more `*.sql` files to initialize an empty database
- `migrations/` (optional): ordered `*.sql` files (e.g. `0001_gate_sm.sql`) that are safe to apply multiple times

You can change these paths via env vars in `apps/wow/db-migrate-cronjob.yaml`.

