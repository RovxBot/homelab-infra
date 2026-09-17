# SoloCollections rollout

This realm replaces `azerothcore/mod-transmog` with the server component of
[SoloCollectionsPlatform](https://github.com/haha2345/SoloCollectionsPlatform):
`haha2345/mod-solo-collections@v0.2.0`. The module version is deliberately
pinned so the server can be deployed with the matching client AddOn release.

## Server deployment

`apps/wotlk/config/modules.txt` supplies the module to the image build. Its
`include.sh` registers the module's tracked auth, characters, and world SQL
with AzerothCore. The `db-import` init container is configured to apply all
module updates before worldserver starts, so do not manually import the module
base schema or its update files into this existing realm.

Before building the replacement image, take a restorable backup of the
`acore_auth`, `acore_characters`, and `acore_world` databases. The old
transmogrification tables and the existing compatible NPC spawns are left in
place; this change does not delete player appearance data.

1. Run **Build AzerothCore WotLK images** with `build_scope=all`. This rebuilds
   both `worldserver` and `db-import`, which must always be deployed together
   for a module/database change.
2. Review and merge the workflow-created manifest PR, then let Flux roll out
   the new images. Do not update a worldserver image without its matching
   `db-import` image.
3. Check the worldserver log for `startup_versions`, `build_info`,
   `schema_check result=ready`, and `provider_registry result=ready`.
4. As an in-game administrator, run `.solocollections status`. Resolve a
   schema, mapping-hash, or build-metadata error before distributing the
   client AddOn.

The rendered `transmog.conf` selects `SoloCollections.Backend = Cpp`, enabling
the server-authoritative SC2 backend. Armor and weapon mixing both start at
`same`; broaden either rule only after gameplay testing. The retained
`Transmogrification.*` options govern the module's compatible NPC workflow.

## Client rollout

The server module alone does not provide the collections UI. After the server
checks above pass, install the **matching v0.2.0**
`SoloCollections/addon/SoloCollections` directory from
SoloCollectionsPlatform into every WoW 3.3.5a client's
`Interface/AddOns/SoloCollections` directory, then restart or reload the
client. Do not run its legacy SC1/ALE collection backend in parallel.

Verify `/sc` (or `/collections`) opens the journal and `/tmog` opens the
wardrobe. If the UI reports an SC2 or build-hash mismatch, remove the AddOn
from circulation until the server and client are on the same release.
