# Hosted Gitea MCP

The Kubernetes `gitea-mcp` Deployment serves Streamable HTTP at
`https://git.cooked.beer/mcp`. No workstation service or OpenAI tunnel client is
needed. Cloudflared routes `/mcp` and the bare and `/mcp` variants of
`/.well-known/oauth-protected-resource` to the MCP Service. The existing Gitea
route handles all other paths, including its OAuth login and token endpoints.

## Architecture and authorization

| Choice | Benefit | Trade-off |
| --- | --- | --- |
| Official Gitea MCP 1.12.0, pinned by image digest | Same implementation and 21 tools as Codex | Upgrades require reviewing upstream behavior and the tool allowlist |
| Existing Gitea hostname and Cloudflare Tunnel | No new DNS, NodePort, public origin or workstation daemon | Cloudflare and Gitea HTTPS availability are dependencies |
| Native Gitea OAuth with public client and PKCE S256 | Each user signs in with their own Gitea identity; no shared operator token in the pod | ChatGPT's exact callback must be registered; Gitea has no dynamic client registration |
| Single stateless replica | No database, PVC or credential store for MCP | Brief interruption is possible during a node failure; Kubernetes replaces the pod |

The server requires authentication before MCP initialization, tool discovery
and tool calls. Health and OAuth resource discovery are public. Missing and
rejected credentials receive HTTP 401 with an OAuth challenge. No Gitea access
token is mounted in the Deployment; OAuth mode explicitly disables static-token
fallback. Kubernetes API credentials are not mounted. The process runs as a
non-root user with a read-only root filesystem and a small temporary filesystem.

`GITEA_TOOLS` enforces the same 21-tool allowlist as the installed Codex
configuration, including issue, label, PR, Actions and release writes. Repository
and branch writes, user administration and secret-management tools are excluded.
OAuth scopes should be `write:repository write:issue read:user`, matching the
Codex token. The upstream resource metadata advertises broader scope categories;
override the requested scopes in ChatGPT's advanced OAuth settings.

Gitea permissions follow the **signed-in user**, not the OAuth application's
owner. Codex uses the non-administrator `codex-dev` account. Signing in as
`codex-dev` matches its repository permissions; signing in as `sam` uses Sam's
permissions with the same server tool allowlist. Do not claim that those two
identities have identical repository access. NetworkPolicy is documentary while
Cilium policy enforcement is disabled; authentication is the access boundary.

## ChatGPT setup

Create a **public** OAuth2 application in Gitea under Settings → Applications.
Leave Confidential Client unchecked and secondary authorization enabled. Use
only the exact redirect URI shown by ChatGPT. Client registration is a one-time
Gitea operation, separate from the Kubernetes Deployment. A public PKCE client
does not require a client secret.

In ChatGPT's Add custom MCP server form:

| Field | Value |
| --- | --- |
| Name | Gitea |
| Description | Access my Gitea repositories, issues, pull requests, Actions and releases |
| Connection | Server URL |
| Server URL | `https://git.cooked.beer/mcp` |
| Authentication | OAuth |
| Client setup | Static/preconfigured client, using the registered Gitea client ID |
| Registered client ID | `bd98785a-a23e-4c3e-b162-8d1696f2adb6` |
| Client secret | Leave empty; this is a public PKCE client |
| Authorization URL, if requested | `https://git.cooked.beer/login/oauth/authorize` |
| Token URL, if requested | `https://git.cooked.beer/login/oauth/access_token` |
| Token endpoint authentication, if requested | `none` |
| Scopes | `write:repository write:issue read:user` |

Gitea publishes OAuth discovery at
`https://git.cooked.beer/.well-known/openid-configuration`, including PKCE S256.
If recreating the ChatGPT connection changes its callback URL, register that
exact URL in Gitea before signing in. Do not enable dynamic client registration
or use a Gitea personal token as a client secret.

The current public application is Gitea OAuth application 6, **ChatGPT Gitea
MCP**, registered on 11 November 2026 for exactly
`https://chatgpt.com/connector/oauth/rCoFiHpvW4B7`. Its client ID is public
configuration, not a credential. No client secret is needed or stored in Git.

Install the resulting plugin, sign in to the intended Gitea account, and verify
the identity using `get_me` and a repository read. Write tools use ChatGPT's
normal confirmation controls. Changing the server tool list requires refreshing
the ChatGPT plugin.

## Release and verification

The operator opens a focused PR. Repository render, schema, Secret-reference and
Kyverno checks must pass before merging. Flux reconciles `apps-gitea` to create
the Deployment and Service and `infrastructure` to roll Cloudflared's generated
configuration and update Gatus. No existing Gitea data workload changes.

Verify the Deployment rollout, both Flux Kustomizations, `/healthz`, HTTP 401
for missing/invalid credentials, the resource discovery document, MCP
initialization, exact 21-tool discovery and a `get_me`/repository read. Use
credentials through private environment/file inputs only; do not place them in
chat, command arguments, manifests or captured request logs. A PAT-based MCP
check verifies transport and authorization, but does not prove ChatGPT's browser
OAuth exchange; record that check separately after the user signs in.

Gatus monitors internal health and public unauthenticated rejection. A 401 is
the expected public health result; a successful anonymous MCP response is a
failure. A Cloudflared rollout or origin error can temporarily produce 404/502
until both Flux reconciliations complete.

Rollback through a reviewed Git revert of the MCP manifest, routes and monitors,
then verify Flux and the normal Gitea website. The MCP server has no persisted
data or schema migration. Uninstall the ChatGPT plugin and revoke its Gitea
authorization if retiring the integration. Removing the OAuth application is
separate and should happen only after confirming no connection still uses it.

References: [Gitea MCP 1.12.0](https://gitea.com/gitea/gitea-mcp/src/tag/v1.12.0/README.md),
[ChatGPT custom MCP setup](https://developers.openai.com/api/docs/guides/custom-mcp-server),
[ChatGPT OAuth requirements](https://developers.openai.com/plugins/build/auth).
