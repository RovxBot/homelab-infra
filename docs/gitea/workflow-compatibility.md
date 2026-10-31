# Workflow compatibility

The initial review covered all fourteen workflows, `CMakePresets.json` and all
24 files in `Tools/Porting` at product commit
`82b1681172b44cecff75cf579c79b63c90961e14`. `workflow-inventory.json` records
their hashes, original platform matrices and timeouts. Source mirrors can
advance normally; each run's checkout log identifies the code actually built.

## Common execution contract

Linux is real Ubuntu 24.04/amd64 with Python **3.12.3**, Clang/Clang++ **18.1.3**,
CMake **3.28.3**, Ninja **1.11.1**, Git/LFS, Bash and Node **24.21.0**.
The presets require CMake 3.21 or newer and use Ninja/Clang on Linux. Ordinary
and ASan/UBSan presets keep the original fixture, compile, graph and runtime
assertions. Leak detection remains enabled. Windowing presets use the exact
SDL3 commit `7f3ae3d57459e59943a4ecfefc8f6277ec6bf540`, Wayland/X11 development
libraries, Mesa, Xvfb and xauth. See `runners.md` for the sanitizer identity and
the narrowly scoped tracing permission required by Talos.

Every original workflow supports push, pull_request and workflow_dispatch,
with GitHub path filters and concurrency expressions. The separate Gitea harness
supports push-to-main smoke checks and manual full dispatch. The native PR
exercise merges into a scratch main branch to trigger those local checks.
Original GitHub YAML is retained unchanged. GitHub path filtering/concurrency
is not claimed to have identical semantics in the separate harness.

All original jobs pin `actions/checkout` to
`11bd71901bbe5b1630ceea73d27597364c9af683` (4.2.2) and upload-artifact to
`ea165f8d65b6e75b540449e92b4886f43607fa02` (4.6.2). Both complete Git histories
are explicitly copied into local `actions` repositories. The harness performs
authenticated local Git checkout directly and uses the pinned local upload
action with runner compatibility patches. The artifact round-trip uses local
download-artifact `d3f86a106a0bac45b974a628896c90dbdf5c8093` (4.3.0).

Artifacts retain the original 14-day policy and fail when expected files are
absent. Every row uploads through the local action, including failure evidence.
The shared artifact path is `artifacts/<preset>/` in the product checkout except
the two explicit exceptions below. Compile artifacts and repository snapshots
also enter the independent cold backup. GitHub API artifact URLs are not used.

## All fourteen workflows

In the table, “+asan” means both the named Linux preset and its `-asan` variant.
“Three OSes” means Linux amd64, Windows amd64 and macOS arm64 in the original
matrix. Native Windows uses MSVC/Windows SDK and Python (`python`); macOS uses
Apple Clang/SDK, Ninja and Python (`python3`). Those native platforms are
unavailable until real hosts are provisioned; none of their labels is offered
by the Kubernetes runner.

| Workflow | Original OS/architecture | Local Linux presets | Extra dependencies | Artifact exception | Cross-repository input | Execution evidence |
| --- | --- | --- | --- | --- | --- | --- |
| m0-audit.yml | Linux amd64 | Python source/classifier audit | No compiler needed | `artifacts/m0-source-audit.json` | Local reference SHA `959b0b8…` | Full-run ledger |
| m1-core.yml | Three OSes | linux-core +asan | Core/hash/services | — | None | Smoke and full-run ledger |
| m2-assets.yml | Three OSes | linux-assets +asan | Synthetic assets/archive fixtures | — | None | Full-run ledger |
| m2-async.yml | Three OSes | linux-async +asan | Threads/async fixtures | — | None | Full-run ledger |
| m2-files.yml | Three OSes | linux-files +asan | Synthetic file services | — | None | Full-run ledger |
| m2-shell.yml | Three OSes | linux-window +asan, linux-window-dynamic-asan | SDL3/X11/Wayland | — | None | Full-run ledger |
| m3-framescript.yml | Three OSes | linux-framescript +asan | Tracked FrameScript/Lua sources | — | None | Full-run ledger |
| m3-gl.yml | Linux amd64 | linux-gl +asan | SDL3, Xvfb, Mesa/GLX | — | None | Full-run ledger |
| m3-layout.yml | Three OSes | linux-layout +asan | Tracked layout/UI fixtures | — | None | Full-run ledger |
| m3-lua.yml | Three OSes | linux-lua +asan | Tracked Lua/FrameScript sources | — | None | Full-run ledger |
| m3-packets.yml | Three OSes | linux-packets +asan | Synthetic packet fixtures | — | None | Full-run ledger |
| m3-xml.yml | Three OSes | linux-xml +asan | Tracked XML/UI sources | — | None | Full-run ledger |
| native-client.yml | Linux amd64 | linux-client +asan | SDL3, Xvfb, Mesa/GLX | — | None | Full-run ledger |
| windows-baseline.yml | Windows amd64 | Unavailable on Linux | PowerShell 7, VS2022/MSVC/Windows SDK, tracked Windows libraries | `artifacts/windows-baseline/` | None | Unavailable: no native host |

The generator produces thirteen Linux job definitions and **26 matrix jobs**.
Actual per-job outcomes, failures and reruns are recorded in `operations.md`;
configuration and local image smoke tests do not count as project execution.
The initial failures exposed private user namespaces disabled by Talos,
LeakSanitizer's ptrace requirement, root tar ownership without CHOWN, and SDL
sources outside the audited dependency directory. Infrastructure/adaptation
fixes addressed these without relaxing the project assertions.

The recovered Gitea server, both project mirrors and all three action copies
are required inputs. The source token in the harness has **read:repository**
only; it is not the mirror bot's repository-write token. Each job proves
`github.com` resolves to the test sink and HTTPS access fails before fetching
sources. GitHub API/raw/codeload/object/registry hosts are also mapped away in
that pod template. This is an outage exercise, not a Cilium isolation claim.
