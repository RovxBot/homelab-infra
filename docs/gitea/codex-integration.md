# Workstation and Codex

Keep `origin` pointing to GitHub during normal operation. Add a separate remote;
production pull mirrors reject pushes until explicitly promoted during failover.

```bash
git remote add gitea ssh://git@192.168.1.190:30222/github-dr/wowdecomp.git
git fetch gitea --tags
git remote -v
```

For the reference repository use `github-dr/wowdecomp15595.git` and branch
`w15595`. Every node can serve the private NodePort; replace the node address
if metal0 is unavailable. The existing WireGuard gateway can reach these LAN
addresses. Do not forward the SSH port from the public OCI edge.

Generate a workstation key with `ssh-keygen -t ed25519`, protect it with a
passphrase, load it using `ssh-add`, and add only the public key in Gitea user
settings. Verify the server host-key fingerprint through an authenticated
Gitea administrator session or the offline recovery inventory before accepting
it. Do not blindly trust a new `ssh-keyscan` result. Test `ssh -p 30222
git@192.168.1.190`; Gitea's no-shell greeting is expected.

The initial authenticated ED25519 host-key fingerprint was
`SHA256:h8qUFeYXWmL7bu6Qj9cTrlV0hAzi2cFgXJDPLU4JPLI`. Private SSH clone and
branch push were verified from the Arch workstation using a temporary test key.
Re-verify identity after an intentional host-key rotation or restore.

After the promotion runbook, normal branch development is:

```bash
git clone ssh://git@192.168.1.190:30222/github-dr/wowdecomp.git
cd wowdecomp
git switch -c recovery/my-change
code .
# Edit, test and commit locally.
git push -u origin recovery/my-change
```

Open a native pull request in Gitea. To try this before an incident, use a
disposable `ci/dr-test-*` repository; leave the production mirror read-only.
For recovery administration, forward the HTTP Service to localhost rather
than sending a password over LAN HTTP:

```bash
kubectl -n gitea port-forward service/gitea-http 13000:3000
```

Codex CLI and the VS Code extension can work on these local clones. Start
`codex` in the checkout, or open that folder in VS Code and select local
execution. GitHub does not need to be a remote for editing or running local
commands. Model access still requires the OpenAI service and valid sign-in;
this is GitHub independence, not an offline model. GitHub-connected Codex Cloud
tasks are a separate integration and are not a Gitea cloud-runner replacement.
See [the IDE documentation](https://learn.chatgpt.com/docs/codex/ide) and
[cloud documentation](https://learn.chatgpt.com/docs/cloud).

## Read-only Gitea MCP example

Use the maintained [Gitea MCP server](https://gitea.com/gitea/gitea-mcp), pinned
to release **1.12.0**. Download its Linux x86_64 archive and checksum manifest
from that release, verify the archive checksum, and install the binary as
`~/.local/bin/gitea-mcp-1.12.0`. Back up the verified binary for recovery; do not
use `go run ...@latest` at startup.

Create a dedicated Gitea viewer with read membership only on the protected
repositories and the intended CI repositories. Issue a token with
`read:repository` and `read:issue`; add another read scope only if a specific
read endpoint requires it. Avoid administrator, organization write, package
write and repository write permissions. Read-only server mode supplements the
token and team permissions; it does not replace them.

Load the token from your password manager into `GITEA_ACCESS_TOKEN`. Copy the
settings from `ops/gitea/codex-mcp.example.toml` into your private Codex
configuration. The launcher exposes repository, tree, branch, issue, PR and
Actions status/log/artifact read tools, and forwards a small environment that
excludes Kubernetes and cloud credentials. It exposes no write tools and no
Actions secret-management tools. Set `GITEA_HOST=http://127.0.0.1:13000` when
using the recovery port-forward. Never commit a token or place it in command
arguments or a workspace file.

Codex CLI and the extension share MCP configuration. Verify using `codex mcp
list`; then start a new session and inspect the tool list. This example does
not modify the workstation's global configuration automatically. See the
[official MCP configuration documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
