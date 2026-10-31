# Actions runners

Gitea runner **5.2.4** has a native Kubernetes backend, verified against its
release documentation and example manifests. Kubernetes 1.34.1 satisfies its
1.30 minimum. This deployment uses job pods; there is no host Docker daemon,
Docker socket, privileged container or Docker-in-Docker service.

The controller runs in `gitea-runners`, creates/executes/removes job pods and
temporary Secrets only in `gitea-jobs`, and is registered to the **ci**
organization. It cannot read existing Secrets or mutate deployments, nodes or
other namespaces. Ordinary jobs have no Kubernetes token, run as uid 1000 with
dropped capabilities and RuntimeDefault seccomp. Talos currently has
`user.max_user_namespaces=0`; requesting private user namespaces prevented pod
sandbox creation. Jobs use normal Kubernetes user namespaces without changing
that platform setting. The runner registration file and recent logs persist on
a retained PVC. Initial concurrency is one, with a 45-minute ceiling, per-job
CPU/memory/ephemeral-storage limits, namespace quotas and orphan cleanup.

Talos also sets `kernel.yama.ptrace_scope=2`. LeakSanitizer cannot inspect its
threads under that restriction without SYS_PTRACE. Non-root containerd exec
drops effective capabilities; sanitizer job pods therefore use UID/GID 0 with
**only SYS_PTRACE**, no privilege escalation, RuntimeDefault seccomp and normal
private pod PID/network namespaces. Their emptyDirs use group 0 so tar can
extract runner inputs without granting CHOWN. No privileged container, host
PID/network, hostPath, API token or other added capability is allowed.

The new `gitea-jobs` namespace delegates these controls to the fail-closed
namespaced `gitea-job-security` Kyverno Policy. Its PSA admission label is
`privileged` because PSA cannot express the narrow exception; audit/warn remain
restricted. The policy still enforces the other restricted controls, one pinned
job image/container, resource declarations and only ephemeral/runner-Secret
volumes. Normal jobs must stay UID 1000. The explicit `dr-sanitizer` label selects
the bounded exception. `check-job-policy.py` verifies allowed normal/sanitizer
pods and rejects privileged mode, root in a normal job, host PID/path, tokens,
extra capabilities, unlabelled ptrace, missing drop-ALL, unconfined seccomp,
sidecars and unapproved images. Run it with `--server` to test actual admission.

Network isolation is limited by the existing Cilium `never` setting. Only run
trusted local code. A credential-free pod can still reach the trusted LAN and
cluster network; this is not an untrusted public pull-request build service.
Network policy enforcement is a separate platform change, outside this plan.

`ops/gitea/images/Dockerfile` builds Ubuntu 24.04 with Python 3.12, Clang 18,
sanitizer runtimes, CMake, Ninja, Git/LFS, Node 24.21.0 and X11/Wayland/SDL/Mesa/
Xvfb/xauth dependencies. Package inputs use a dated Ubuntu snapshot and the
image base is digest-pinned. The exact SDL Git archive requested by the project
is checksum-verified and unpacked into the image. `CMAKE_TOOLCHAIN_FILE`
copies SDL to each preset's expected `build/_deps/sdl3-src` location and sets
disconnected mode. Using `/opt` directly failed the source graph audit; its
permitted roots were preserved. The project's
compile, graph and sanitizer assertions are preserved. The `SDL_URL` build
argument can point to an off-host copy of that archive during recovery.

The image publishing workflow bootstraps through GitHub during normal
operation. Preserve the final image, base images, Node tarball, SDL archive,
APT package inputs and Dockerfiles independently; GHCR availability is not an
offline image distribution guarantee. An OCI export was also built locally
using a rootless builder without a host Docker daemon. Do not leave a builder
with process sandboxing disabled available for other users or untrusted jobs.

The adaptation generator reads all fourteen GitHub workflows, CMake presets
and every Porting script. It generates thirteen Linux job definitions, retaining
their Linux matrices and test commands. It removes package installation steps
because the immutable image provides the dependencies. It never changes the
GitHub repositories or runs Windows/macOS jobs in Linux containers. Source
checkouts and pinned action downloads use internal Gitea URLs. Gitea scans only
`.gitea/workflows`; original `.github/workflows` remain historical input.

Runner 5.2.4 compatibility patches support the upstream artifact v4 actions
used here. Keep `patch_actions: true`, cache v2 enabled, and the runner's cache
Service reachable from job pods. Actual upload/download tests remain acceptance
checks; see `operations.md` for the actual execution results.

## Native Windows

No available Windows host was verified. Provision a dedicated Windows Server
2022 x64 host with Visual Studio 2022 Build Tools, Desktop C++ workload, Windows
SDK, MSVC v143, CMake, Ninja, Python 3, Git/LFS, PowerShell 7 and Node.js. Use
the pinned Windows amd64 runner binary from the 5.2.4 release and verify its
published checksum. Register to `ci` with the **only** label
`windows-2022:host`. Install it as a service under an unprivileged build account
that has no administrator or cluster credentials. Restrict access to trusted
repositories because host jobs share that account's environment. Run
`Tools/Porting/Build-WindowsBaseline.ps1` without modifying its x64 generator,
tracked-library, signature or crash-logger assertions. Restore artifacts locally
and record an actual successful run before declaring coverage.

Create an unprivileged local account `giteaci` and `C:\GiteaRunner`, granting
only that account access to its `.runner` file. Download
`https://gitea.com/gitea/runner/releases/download/v5.2.4/gitea-runner-5.2.4-windows-amd64.exe`.
Verify `Get-FileHash -Algorithm SHA256` equals
`ec4df88c593289e24e7f93dea244f5867ebe9fabe8e542edc8b5e78c7d0b4166`.
Copy `ops/gitea/native/windows.yaml` and register as that account:

```powershell
cd C:\GiteaRunner
# Populate this environment variable securely from the ci organization runner
# registration token, then remove it after registration. Do not echo its value.
.\gitea-runner.exe --config windows.yaml register --no-interactive `
  --instance https://git.cooked.beer --name cooked-windows `
  --labels windows-2022:host
Remove-Item Env:GITEA_RUNNER_REGISTRATION_TOKEN
.\gitea-runner.exe --config windows.yaml daemon
```

For startup, create a Task Scheduler task for the `giteaci` account, running
whether logged in or not, with **highest privileges unchecked**, working
directory `C:\GiteaRunner`, executable `gitea-runner.exe` and arguments
`--config windows.yaml daemon`. Grant log-on-as-batch only as required. Do not
register a console binary directly as an unsupported Windows Service.

## Native Apple Silicon

No Apple Silicon host was verified. Provision real Apple hardware running
macOS 14, Xcode/Command Line Tools and its SDK, CMake, Ninja, Python 3, Git/LFS
and Node. Verify `uname -m` reports `arm64`. Install the checksum-verified Darwin
arm64 runner 5.2.4 and register **only** `macos-14:host` to `ci`. Run through a
dedicated launchd user account with no cloud or Kubernetes credentials. Match
`RUNNER_ARCH=ARM64`; the project's scripts verify both the host and each Mach-O
binary using `lipo`. Run the actual macos-arm64 presets. Cross compilation and
emulation do not satisfy this acceptance gate.

Download `gitea-runner-5.2.4-darwin-arm64` from the same release and verify
`shasum -a 256` equals
`d3b6375c1a38e7c56348f9d6e59afc7ed048bd749b3d050c37129bda8fea7467`.
As `giteaci`, place the executable and `ops/gitea/native/macos.yaml` in
`/Users/giteaci/runner`, with directory mode 0700. Register using
`GITEA_RUNNER_REGISTRATION_TOKEN` and:

```bash
./gitea-runner --config macos.yaml register --no-interactive \
  --instance https://git.cooked.beer --name cooked-apple-silicon \
  --labels macos-14:host
unset GITEA_RUNNER_REGISTRATION_TOKEN
./gitea-runner --config macos.yaml daemon
```

After the foreground test passes, install the supplied launchd plist under
`/Library/LaunchDaemons` as root:wheel mode 0644 and bootstrap it with
`sudo launchctl bootstrap system /Library/LaunchDaemons/beer.cooked.giteaci.plist`.
The daemon's `UserName` keeps execution under `giteaci`. Confirm its PATH sees
the required native compilers, SDK and Python before accepting work.

For either platform, copy only the original native matrix entries into a
reviewed `.gitea/workflows` adaptation in the writable harness. Use the local
pinned checkout/upload actions, source-read token and a Gitea URL reachable by
that host (cluster DNS is not a workstation DNS name). Keep the original native
test scripts, SDK/architecture/signature checks and artifact paths. Pre-stage
the checksum-verified SDL source archive from recovery inputs and supply its
native path through a local toolchain file while retaining the expected
`build/_deps/sdl3-src` audit path. Run every native matrix entry and restore its
artifact before changing the acceptance ledger from unavailable to verified.
