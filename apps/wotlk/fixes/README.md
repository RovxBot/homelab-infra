# WotLK Fixes and Updates

This folder is for small, targeted fixes and updates applied on top of the
server stack (e.g., Smart AI tweaks, DB hotfixes, or scripted adjustments).

Suggested layout:
- `sql/` for database patches and queries.
- `notes/` for context, links, and validation steps.
- `scripts/` for small helper scripts (idempotent where possible).

Please include:
- A short description of the issue being fixed.
- The source of the fix (issue URL, commit, or forum post).
- Any rollout or verification steps.

## Applied data fixes

- [Cooking reagent correction](notes/cooking-reagents.md): sync obsolete recipe
  ingredients in the server's `Spell.dbc` with the pinned AzerothCore client data.
