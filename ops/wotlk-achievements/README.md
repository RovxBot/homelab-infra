# Realm First achievements

This repo can **configure rewards** for existing WotLK `"Realm First!"` achievements and **revoke** them from bot accounts.

## Limitations

- AzerothCore achievements are defined by client DBCs. You **cannot create new achievements** like “Realm First 60/70” without shipping client-side DBC changes.

## What’s included

- `ops/wotlk-achievements/realmfirst.sql`
  - Ensures the raid realm-first achievements reward their matching built-in titles:
    - `456` → title `139` (`Obsidian Slayer`)
    - `1400` → title `120` (`the Magic Seeker`)
    - `1402` → title `122` (`Conqueror of Naxxramas`)
    - `3117` → title `158` (`Death's Demise`)
    - `3259` → title `159` (`the Celestial Defender`)
    - `4078` → title `170` (`Grand Crusader`)
    - `4576` → title `174` (`Bane of the Fallen King`)
  - Deletes any `"Realm First!"` achievements from **bot characters** (bot accounts are detected via `acore_playerbots.playerbots_account_type.account_type = 1`).

- `apps/wotlk/bot-realmfirst-cleanup-cronjob.yaml`
  - A suspended-by-default CronJob that runs `realmfirst.sql` against the DB (safe to run multiple times).
  - The SQL mounted by the CronJob lives at `apps/wotlk/realmfirst.sql`.

- `ops/wotlk-achievements/extract_realmfirst.py`
  - Helper to extract `"Realm First!"` achievement IDs from `Achievement.dbc`.
