# Memos image assessment — 22 November 2026

Tracking [#2135](https://github.com/RovxBot/homelab-infra/issues/2135) stays open.
This phase extends the merged posture baseline at `10520c0f` without changing
chart 1.42.0, admission enforcement, Memos or Cilium.

## Qualification

Verified locally: exact 61-object render, 48 Kyverno CEL passes without failures,
errors, skips or warnings; live server-side admission dry-runs accept the render.
Additional objects over the 47-object posture baseline: two Deployments, two
Services, two ServiceAccounts, one PVC, one ConfigMap, one CronJob, one
ClusterRole, two ClusterRoleBindings, one Role and one RoleBinding. Node/host
collectors, external reporting and automatic image routing remain disabled.

Source reviewed at immutable tags: Kubevuln v0.3.578 commit
`45d265e3b46af4d7fc1c896ecc22a93915f2d6fe`, operator v0.2.209 commit
`09a2e5030d4b79b727830763e59e644ae5b3a74a`. Kubevuln's go.mod pins Grype
v0.121.0 and Syft v1.55.0. The [pinned operator watcher implementation](https://github.com/kubescape/operator/blob/09a2e5030d4b79b727830763e59e644ae5b3a74a/mainhandler/handlerequests.go)
starts image watchers independently of continuous posture scanning. The explicit
operator component override prevents startup, Pod, SBOM and ContainerProfile
image triggers. Direct scan API authorization remains mandatory.

## Database provenance and freshness

| Artifact | Verified identity |
| --- | --- |
| Official distribution | `quay.io/kubescape/grype-offline-db` |
| OCI index SHA-256 | `9dd3b2eb49d80612866cfb13f7da0038fe9e3d4108784881de9a7b554a29815a` |
| amd64 manifest | `sha256:71ec8c69b03677717a126ccf78cd28453e0a226886c44ba055000afb4c088eca` |
| DB layer SHA-256 | `553cbb4b2a278626a1db1713f13b3b322eb040f688be9a360ee02e23bef97599` |
| Image creation | `2026-11-21T01:05:33.753515764Z` |
| Database build | `2026-11-20T06:21:00Z` |
| Schema | `v6.1.10` |
| Archive | `vulnerability-db_v6.1.10_2026-11-20T00:20:17Z_1795155660.tar.zst` |
| Archive SHA-256 | `6866d1685025e6001d422fb4be59f26feebacb151b3ff77b012d999e12b9af24` |
| Archive / expanded SQLite | 205,235,440 / 2,679,586,816 bytes |

Downloaded registry index/platform manifest, config and every layer; content
hashes verified, archive bytes match mirror `latest.json`, SQLite `db_metadata`
confirms build/schema and `PRAGMA quick_check` returns `ok`. Registry TLS and
content addressing establish Kubescape distribution integrity. No independent
signature or byte-for-byte match to the original Anchore archive was established.
Do not infer that trust from a checksum distributed in the same mirror.

The [publisher index](https://grype.anchore.io/databases/v6/latest.json), read on
22 November, reports build `2026-11-21T06:20:06Z`, archive SHA-256
`921d1b1b4151a211d39efbb8a7da54caabfc6eb717112766cb5bdee7920809b5`.
The selected mirror is **one daily edition behind**, approximately 42 hours old
at qualification, within the explicit 72-hour limit for this bounded canary.
It is a recent snapshot, not the newest publisher database. Direct workstation
connections to both Anchore database hosts failed; the publisher metadata was
independently retrieved through web access. The [runbook](README.md#freshness-and-updates)
requires a new age/integrity check before every future assessment and a reviewed
immutable update; neither a cache nor healthy readiness proves freshness.

The pinned [Kubevuln DB loader](https://github.com/kubescape/kubevuln/blob/45d265e3b46af4d7fc1c896ecc22a93915f2d6fe/adapters/v1/grype.go)
uses the configured local listing URL and rechecks it after 24 hours. Its
installation configuration does not supply Grype age/checksum validation flags;
we therefore require explicit external integrity/freshness evidence. DB readiness
and successful scan completion alone cannot meet that requirement.

## Production acceptance

Not run at PR preparation. Production results, immutable image correlation,
timestamps, measured overhead and final safety checks will be recorded here
after qualification and Flux deployment. Raw artifacts are retained locally
under `.work/task23/`; no clean-scan claim follows from absent results.
