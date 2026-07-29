# Realm progression (WotLK)

Source of truth: `apps/wotlk/config/progression_system.conf` and the
[`azerothcore/mod-progression-system`](https://github.com/azerothcore/mod-progression-system)
module.

The realm uses global, bracket-based progression. It does not keep a separate
progression state for each character. All enabled brackets are cumulative:
`Bracket_0` creates the locked baseline, and each subsequent bracket applies
the unlocks for that stage.

## Current state: The Burning Crusade phase 1

The configured brackets unlock:

- TBC levelling and 61--69 dungeons
- Level-70 normal dungeons and heroics
- Gruul's Lair and Magtheridon's Lair (`Bracket_70_2_1`)
- Karazhan (`Bracket_70_2_2`)

Later content remains disabled, starting with Ogri'la (`Bracket_70_2_3`),
Serpentshrine Cavern, and The Eye. This also keeps Hyjal, Black Temple,
Zul'Aman, Sunwell, and all WotLK content locked.

## Advancing the realm

Enable the next bracket or brackets in `progression_system.conf`, deploy the
updated image/module list, and allow the database-import job to apply the new
module SQL. Do not disable an already-applied bracket to roll the realm back:
the module's SQL changes are persistent. Restore a known-good world-database
backup before attempting a rollback.

## Migration note

`mod-individual-progression` also makes persistent world-database changes.
Before deploying this module to an existing realm, take a backup and rebuild or
restore the world database to a clean AzerothCore baseline, then let the
database-import job apply `mod-progression-system` updates. Removing the old
module alone does not undo its SQL changes; mixing both modules' database
changes can leave stale gates, spawns, or loot settings.
