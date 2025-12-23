# Individual Progression (WotLK) — Current Gates

This cluster runs the `mod-individual-progression` module, which keeps **progression per character** (stored via Player Settings).

Your current defaults come from the module config generated in the `wotlk-worldserver` pod:

- `/azerothcore/env/dist/etc/modules/individualProgression.conf`

## What “progression” means

The module advances your character’s progression when you complete key raid/content milestones. It then uses that progression state to:

- Lock/unlock raids and zones
- Enforce leveling caps (60/70) until content is cleared
- Optionally enforce group rules (only group with players in the same phase)

## Progression states (what unlocks what)

This is the module’s built-in state machine (names match the code). “Unlocked after” means “you must reach at least this state”.

### Vanilla

| State | Name | Set by | Unlocks / Gates |
|---:|---|---|---|
| 0 | `PROGRESSION_START` | New character | Vanilla world (baseline) |
| 1 | `PROGRESSION_MOLTEN_CORE` | Ragnaros kill (MC) | **BWL becomes accessible** |
| 2 | `PROGRESSION_ONYXIA` | Onyxia kill | Onyxia completion tracked (module uses this for some checks) |
| 3 | `PROGRESSION_BLACKWING_LAIR` | Nefarian kill (BWL) | ZG + AQ chain becomes relevant (see ZG gate below) |
| 4 | `PROGRESSION_PRE_AQ` | Quest `BANG_A_GONG` / `SIMPLY_BANG_A_GONG` | AQ20/AQ40 instance access (gates open) |
| 5 | `PROGRESSION_AQ_WAR` | Quest `CHAOS_AND_DESTRUCTION` | “War effort complete” stage for outdoor AQ content |
| 6 | `PROGRESSION_AQ` | C’Thun kill (AQ40) | Naxx40 + Scourge Invasion stage |
| 7 | `PROGRESSION_NAXX40` | Kel’Thuzad kill (Naxx40) | “Pre-TBC” event chain becomes relevant |
| 8 | `PROGRESSION_PRE_TBC` | Quest `INTO_THE_BREACH` | **Outland access** + **leveling past 60** |

### TBC

| State | Name | Set by | Unlocks / Gates |
|---:|---|---|---|
| 9 | `PROGRESSION_TBC_TIER_1` | Prince Malchezaar kill (Karazhan) | SSC/TK tier becomes accessible |
| 10 | `PROGRESSION_TBC_TIER_2` | Kael’thas kill (The Eye) | Hyjal/BT tier becomes accessible |
| 11 | `PROGRESSION_TBC_TIER_3` | Illidan kill (Black Temple) | Zul’Aman becomes accessible |
| 12 | `PROGRESSION_TBC_TIER_4` | Zul’jin kill (Zul’Aman) | Sunwell becomes accessible |
| 13 | `PROGRESSION_TBC_TIER_5` | Kil’jaeden kill (Sunwell) | **Northrend access** + **leveling past 70** |

### WotLK

| State | Name | Set by | Unlocks / Gates |
|---:|---|---|---|
| 14 | `PROGRESSION_WOTLK_TIER_1` | Kel’Thuzad kill (WotLK Naxx) | Ulduar becomes accessible |
| 15 | `PROGRESSION_WOTLK_TIER_2` | Yogg-Saron kill (Ulduar) | Trial of the Crusader becomes accessible |
| 16 | `PROGRESSION_WOTLK_TIER_3` | Anub’arak kill (ToC) | Icecrown Citadel becomes accessible |
| 17 | `PROGRESSION_WOTLK_TIER_4` | Lich King kill (ICC) | Ruby Sanctum becomes accessible |
| 18 | `PROGRESSION_WOTLK_TIER_5` | Halion kill (Ruby Sanctum) | End of WotLK progression |

## Hard gates (the “rules you asked for”)

### Level caps

- **Cannot gain XP past level 60** until you reach `PROGRESSION_PRE_TBC` (i.e. complete `INTO_THE_BREACH`).
- **Cannot gain XP past level 70** until you reach `PROGRESSION_TBC_TIER_5` (i.e. defeat Kil’jaeden / clear Sunwell).

### Raid / zone access

- **BWL** is blocked until `PROGRESSION_MOLTEN_CORE` (MC complete).
- **AQ20/AQ40** are blocked until `PROGRESSION_PRE_AQ` (gong quest complete).
- **Outland (world + instances)** is blocked until `PROGRESSION_PRE_TBC` (except Blood Elf / Draenei starting zones).
- **Northrend (instances)** is blocked until `PROGRESSION_TBC_TIER_5` (TBC complete).

## Tunables (current defaults)

These are the config knobs that most directly affect “gates”:

- `IndividualProgression.EnforceGroupRules = 1` (only group with same progression phase)
- `IndividualProgression.RequiredZulGurubProgression = 3` (ZG unlocks after BWL by default)
- `IndividualProgression.SerpentshrineCavern.RequireAllBosses = 1` (must clear all bosses before Vashj’s console)
- `IndividualProgression.TheEye.RequireAllBosses = 1` (must clear all bosses before Kael doors open)
- `IndividualProgression.ProgressionLimit = 0` (no hard ceiling; allow progress through WotLK)
- `IndividualProgression.StartingProgression = 0` (new characters start at 0)

## Notes

- Progression is stored per character (via `character_settings`), not per account.
- If you want these settings fully GitOps-managed (instead of “defaults copied in the pod”), we can mount `individualProgression.conf` from a ConfigMap and keep it in this repo.

