# SoloCollections rollout

This realm replaces `azerothcore/mod-transmog` with the server component of
[RovxBot/SoloCollectionsPlatform](https://github.com/RovxBot/SoloCollectionsPlatform).
That repository contains both the client/catalog source in `SoloCollections/`
and its matching C++ backend in `mod-solo-collections/`. The image workflow
checks out the configured source ref once, then builds the embedded module;
the two sides therefore cannot drift to different SC2 catalog hashes.

## Server deployment

`apps/wotlk/config/solocollections-source.env` selects the RovxBot repository,
ref, and its two embedded roots. `apps/wotlk/config/modules.txt` deliberately
does not list SoloCollections: the image workflow copies the embedded module
to `AzerothCore/modules/mod-solo-collections` itself. Its `include.sh`
registers the tracked auth, characters, and world SQL with AzerothCore. The
`db-import` init container applies module updates before worldserver starts,
so do not manually import the module base schema or update files into this
existing realm.

Before compiling, the workflow generates `SoloCollectionsBuildInfo.inc` from
the same composite checkout and verifies the AddOn/module hashes for SC2 types
10, 11, 12, 13, 14, 16, and 17. A mismatch or `UNPINNED` build fails CI rather
than silently falling back to a character-local journal.

The embedded module captures a C++ structured binding in an SC2 lambda, which
Clang correctly rejects in the realm's C++17 build. The image workflow applies
[a narrowly scoped compatibility patch](patches/mod-solo-collections-cxx17.patch)
that rebinds the same session as an ordinary reference. It does not alter the
SC2 protocol, collection authority, or database behavior. Remove the patch
only after the equivalent C++17-safe fix is committed to the RovxBot fork.

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
checks above pass, install the AddOn from the same configured RovxBot source
ref: `SoloCollections/addon/SoloCollections` into every WoW 3.3.5a client's
`Interface/AddOns/SoloCollections` directory, then restart or reload the
client. Do not run its legacy SC1/ALE collection backend in parallel.

Verify `/sc` (or `/collections`) opens the journal and `/tmog` opens the
wardrobe. If the UI reports an SC2 or build-hash mismatch, remove the AddOn
from circulation until the server and client are on the same release.
