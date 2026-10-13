# Cooking reagent correction (2026-10-13)

Cooking failed with missing Hot Spices even though the client recipe window
showed no spice requirement. The live server's `Spell.dbc` contained pre-3.1
reagent lists. The three reported recipes were Goblin Deviled Clams (6500),
Gooey Spider Cake (3377), and Murloc Fin Soup (3372). Their `spell_dbc` database
overrides were absent, and the progression module had no matching changes.

## Reference and scope

The reference is AzerothCore's [client-data v20.0 bundle](https://github.com/wowgaming/client-data/releases/tag/v20.0):

- `dbc/Spell.dbc`: SHA256 `d5cce1a83550dcfa9eb2f0251dbb11fd24c272534b2b1a9b230924a44d817ab3`
- `dbc/SkillLineAbility.dbc`: SHA256 `4154b833d6a26b9b9ce53851d56cb594f0813c0936a72ec89f933cc69abe42c3`

[`sync_cooking_reagents.py`](../scripts/sync_cooking_reagents.py) identifies
cooking recipes through skill line 185 and the create-item spell effect. It
copies fields 52--67 (eight reagent IDs and eight reagent counts) from the pinned
reference into the existing server file. This preserves spell effects, custom
spell rows, localized strings, and all other data. It validates the binary layout,
reference checksums, and every byte outside the selected reagent fields.

The correction checked 174 recipes and changed 57 reagent lists. This includes
all 37 recipes using obsolete Hot or Soothing Spices, plus recipes with other
obsolete ingredients. Spice Bread retains its Mild Spices requirement. Basic
Campfire is outside this recipe correction.

## Applied files

The active file is `/azerothcore/env/dist/data/dbc/Spell.dbc` on the persistent
`wotlk-data-rwx` volume.

- Original SHA256: `5dd1a2894d55c5d7a09cc4fa69757fa54714bfcecbdbbfdcb2ced6e257c0401c`
- Corrected SHA256: `0537e7672c5c869ec5d62d2c1297249d9f22ff759c90e2c3fc6f671d5d405201`
- Original backup: `/azerothcore/env/dist/data/cooking-reagent-backup-20261013T041708Z/Spell.dbc`
- Change report: `/azerothcore/env/dist/data/cooking-reagent-backup-20261013T041708Z/reagent-changes.json`

The original backup and staged correction were checksum-verified, then the
correction was installed with an atomic rename. Worldserver was restarted through
its administrative `server restart 30` command to reload the DBC data.

## Repeating the repair

Extract `dbc/Spell.dbc` and `dbc/SkillLineAbility.dbc` from the linked `Data.zip`
into a reference directory. Copy the current server file to a local working
directory, then review the changes with a dry run:

```bash
python3 apps/wotlk/fixes/scripts/sync_cooking_reagents.py \
  --input /tmp/server-Spell.dbc \
  --reference-dir /tmp/reference-dbc \
  --report /tmp/cooking-reagent-changes.json
```

Generate the corrected file using `--output /tmp/corrected-Spell.dbc`. Use
`--expected-input-sha256` to guard against changes to the input. The script
creates new files and refuses to overwrite an existing output or modify its input
in place. Back up the live file on the persistent volume, upload to a temporary
file beside it, verify both hashes, and atomically rename the new file to
`Spell.dbc`. Gracefully restart worldserver and check startup and readiness.

The correction survives normal restarts. The client-extraction jobs replace
the DBC directory, so repeat the comparison after extracting new client data.

## Verification and rollback

The repaired file matches the pinned reference for all 174 cooking recipe
reagent lists. A second script run produces zero changes and the same SHA256.
An independent binary comparison confirmed that only the 57 recipe reagent
arrays changed, with the file length, header, string table, and other fields
preserved.

Worldserver restarted at `2026-10-13T04:20:44Z` and returned to `2/2` Ready.
The administrative `server info` response confirmed a fresh uptime and that the
previous session ended with a restart. The installed file retained the corrected
SHA256 after startup. Crafting still needs to be confirmed in the player's client.

For rollback, copy the original backup to a temporary file in the `dbc` directory,
verify its original SHA256, atomically rename it to `Spell.dbc`, and gracefully
restart worldserver. In-game validation: craft the three reported recipes with
their displayed ingredients and no Hot Spices.
