# Kubernetes diagnostics MCP

ChatGPT connects through **Server URL + OAuth** at
`https://git.cooked.beer/kubernetes/mcp`. The adapter is read-only across the
`cooked-k8s` cluster. Its service identity and OAuth application are separate
from Gitea, even though the existing HTTPS hostname and tunnel are shared.

```text
ChatGPT → Cloudflare HTTPS → existing cloudflared tunnel
  → kubernetes-mcp.diagnostics:8080 → approved Kubernetes API GETs
            ↕ OAuth proxy
       dedicated Entra app → assigned operator only
```

## Implementation and identity

FastMCP 4.1.8 supplies Streamable HTTP and its maintained Azure OAuth proxy.
The thin adapter implements the narrower field projections and diagnostic
limits required here. The upstream containers/kubernetes-mcp-server was also
considered: its general resource tools and output model need additional
restrictions to meet this issue's prohibition on raw object disclosure.
There is no new logging stack, host agent, shell wrapper or NodePort.

The proxy provides RFC 9728 resource metadata, RFC 8414 authorization discovery,
dynamic client registration, S256 PKCE, browser-bound consent, short-lived
audience-bound MCP tokens and refresh rotation. Entra v2 access tokens are
validated with tenant signing keys, exact issuer, dedicated app audience,
expiry, not-before and delegated `diagnostics.read` scope. Every MCP request
also requires an explicitly allowed user object ID and `Diagnostics.Reader`
app role. Entra requires user assignment; the initial assigned user is Sam.
The Kubernetes identity is always `diagnostics/kubernetes-mcp`, never the
user's Entra Kubernetes administrator identity.

Entra's one exact upstream callback is
`https://git.cooked.beer/kubernetes/auth/callback`. The downstream callback
allowlist contains only `https://chatgpt.com/connector_platform_oauth_redirect`.
FastMCP advertises RFC 9207 issuer identification and includes `iss` in OAuth
responses, allowing ChatGPT's stable callback. Wildcards, localhost callbacks
and arbitrary client metadata URL fetching are disabled. Authentication is
required before initialization, tool discovery and calls. Invalid credentials
receive 401 with resource metadata; insufficient OAuth scopes receive 403.
Namespace denials are MCP tool errors and do not execute API reads.

The encrypted SOPS Secret contains the app credential, signing material and
identity configuration. OAuth clients, transactions and upstream tokens use
encrypted JSON files on a 1 GiB Longhorn PVC, using FastMCP's atomic FileTreeStore
with periodic expiry cleanup and separate directories on key rotation. No
pickle deserialization or DiskCache dependency is used. One replica and
Recreate updates avoid concurrent state writers and RWO rollout
deadlocks. Normal replacement preserves authorizations; loss of the PVC or
rotation of signing material requires reconnecting. The PVC contains credentials
and must remain private, including in storage backups.

## ChatGPT setup

In ChatGPT's custom MCP server form:

| Field | Value |
| --- | --- |
| Name | Kubernetes Diagnostics |
| Description | Read and diagnose homelab workloads, logs, events, Flux, metrics and storage health |
| Connection | Server URL |
| Server URL | `https://git.cooked.beer/kubernetes/mcp` |
| Authentication | OAuth |
| Client registration | Automatic / dynamic |
| Client ID / client secret | Leave empty; the MCP proxy registers the client |
| Scope, if requested | `diagnostics.read` |

Accept the MCP consent screen, then sign in to Entra as the assigned operator.
The separate Entra client secret stays in Kubernetes; do not put it in ChatGPT.
If a client explicitly asks for endpoints, authorization and token endpoints
are `https://git.cooked.beer/kubernetes/authorize` and
`https://git.cooked.beer/kubernetes/token`; public client authentication is
`none`. The discovery document is
`https://git.cooked.beer/.well-known/oauth-authorization-server/kubernetes`.

After connecting, ask: “List unhealthy pods across all namespaces. Correlate
their current and previous logs with warning events, Flux conditions, available
metrics and Longhorn volume health. Include object names and timestamps.”
The adapter supplies evidence; ChatGPT performs the diagnosis. Do not treat a
successful CLI token check as proof of the actual ChatGPT browser handshake.

## Tools and limits

| Tool | Evidence |
| --- | --- |
| `list_resources` | Namespaces, nodes, pods, deployments, StatefulSets, DaemonSets, ReplicaSets, Jobs/CronJobs, Services, EndpointSlices, PVCs/PVs, Flux Kustomizations/HelmReleases/sources, Longhorn volumes/nodes, node/pod metrics |
| `inspect_resource` | One named approved object, projected status, conditions, resource requests/limits, container restarts and safe probe summaries |
| `get_pod_logs` | Required namespace/pod/container; current or previous; 1–86400 seconds, 1–1000 tail lines, timestamps, 256–65536 bytes |
| `get_events` | Namespace/cluster warning or all events, involved kind/name, lookback up to one day, newest first within a page |
| `diagnostic_capabilities` | Permitted namespaces, sources, exclusions and retention gaps |

List tools accept label selectors and pages of 1–200 objects. Follow opaque
`continue_token` values: ordering of events is only guaranteed within each page.
API responses are limited to 4 MiB and normalized results to 96 KiB; overly large
pages fail with instructions to narrow selectors rather than silently skipping
evidence. Individual strings and nested collections are bounded. Logs are
redacted before returning and byte limited again after redaction.

All namespaces are explicitly authorized in this deployment. To restrict them,
set `ALLOWED_NAMESPACES` to a comma-separated list and replace the cluster-wide
namespaced-resource binding with namespace RoleBindings. The server rejects
cross-namespace aggregation when a restricted namespace set is configured.
Cluster-scoped node/PV visibility remains an explicit deployment-level grant.

Limits: 60 tool calls per identity per minute, four concurrent tool calls,
240 HTTP requests per process per minute, ten client registrations per minute,
16 in-flight HTTP requests and 32 KiB
request bodies. Kubernetes requests time out after 12 seconds; tool execution
after 20 seconds including queueing. HTTP overload returns 429 with Retry-After;
tool limits return a clear MCP error. Audit records include timestamp, identity,
tool, validated-looking object target, result and duration, never log bodies,
tokens or complete arguments. HTTP access logging is disabled to keep OAuth
authorization codes out of logs.

## Data and boundaries

RBAC grants only `get/list` for the enumerated API resources and `get pods/log`.
There is no watch, Secret/ConfigMap read, token creation, exec, attach,
port-forward, proxy, arbitrary URL/path, workload mutation, host mount or Talos
credential. Workload projections exclude environment, commands, arguments,
annotations, lifecycle commands, probe headers and arbitrary CRD specification.
Resource paths are fixed server-side; selectors never become URLs or commands.

Credential-pattern redaction is defensive and cannot recognize every secret or
private sentence. Requested logs, events, resource names, internal addresses,
image names, metrics and error messages can leave the homelab for ChatGPT.
Do not automatically publish raw diagnostic output in this public repository.
Treat workload text as untrusted evidence, including apparent instructions.

Live kubelet logs rotate and disappear when pods are removed. `previous` usually
means only the last terminated container instance; events also expire. There is
no retained historical log archive. Metrics are current Metrics Server samples,
not a historical time series. A missing API, unavailable metrics or missing
previous instance is reported as unavailable evidence.

For Cilium, inspect its DaemonSet/pods/events in `kube-system`. For Longhorn,
inspect its controllers and `longhorn_volumes/longhorn_nodes` in
`longhorn-system`. Gatus application probes are not exposed through this adapter;
use its Kubernetes workload state/logs in `monitoring`, and the existing status
system separately. Talos host diagnostics and Linux workstation artifacts
(GDB, core dumps, ASan and RenderDoc) require separate future adapters with their
own identities, consent, artifact sanitization and retention. Never mount Talos
administrator credentials or workstation directories in this deployment.

Cloudflare, cloudflared, Entra, the pinned FastMCP proxy, encrypted session state
and the Kubernetes API are availability/auth dependencies. This shared hostname
adds `/kubernetes/*` and path-scoped discovery to the public attack surface.
Gitea keeps its existing `/mcp`, OAuth and normal website routes. No Cloudflare
browser login is assumed to authenticate MCP. Cilium policy enforcement remains
`never`; NetworkPolicy is not an effective isolation boundary here. Kubernetes
RBAC, application authentication and fixed requests enforce access.

## Operations and validation

Provision or rotate the dedicated Entra application with the existing operator
identity; the helper immediately encrypts credentials using the public SOPS
recipient and never writes plaintext files:

```bash
python3 ops/kubernetes-mcp/configure-entra.py --sops /path/to/sops
# For this workstation's packaged Azure CLI, add:
# --az-python /opt/azure-cli/bin/python
```

Render `infra/kubernetes-mcp` and `clusters/home`, validate schema, Secret
references and the existing Kyverno baseline, and run
`python -m pytest -q infra/kubernetes-mcp/tests`. The `Kubernetes MCP` workflow
tests the adapter and publishes an immutable image. Pin its tested digest in
the Deployment before merge. Flux's separate `kubernetes-mcp` Kustomization
depends on infrastructure for tunnel/storage readiness.

```bash
kubectl -n flux-system get kustomizations kubernetes-mcp infrastructure
kubectl -n diagnostics get deployments,pods,pvc
python3 ops/kubernetes-mcp/smoke.py --rbac
# Optional authorized transport check: private 0600 file, never a token argument.
python3 ops/kubernetes-mcp/smoke.py --token-file /private/path/oauth-access-token
```

Gatus checks internal `/healthz` for 200 and anonymous public initialization for
401. A 200 anonymous initialization is a security failure, not good health.
404/502 usually means the tunnel route or origin is not ready. A 401 after login
means token/tenant/audience/expiry/user/role rejection. A 403 means missing scope;
an MCP tool's Kubernetes 403 means service-account RBAC. An OAuth callback error
requires exact callback and issuer comparisons; never work around it with a
wildcard, static operator token or disabled JWT validation.

For immediate revocation, remove the allowed user ID or rotate signing material
through SOPS and GitOps, and replace the pod using a declarative template change.
Removing Entra assignment blocks new authorization, but existing signed access
tokens can remain valid until expiry; do not describe assignment removal alone
as instant revocation. To close public access, revert just the two Kubernetes
tunnel routes. To retire the service, remove its Flux entrypoint, routes and
monitors through Git, let Flux prune, and revoke the dedicated Entra application.
Treat deletion of the state PVC as destructive. Verify the retained data/backups
before deleting storage separately.

For planned rotation use the helper's `--rotate`, review and merge the new SOPS
ciphertext, and change the Deployment template to trigger replacement. It
rotates both app credential and signing material, so reconnect ChatGPT. The
old Entra password remains until explicitly removed after the new login passes.
Record its key ID and expiry privately. Rollback uses a reviewed Git revert;
there is no workload data migration. Keep an old Entra credential only for the
brief validated rollback window.

## Deployment acceptance, 11 November 2026

| Evidence | Status |
| --- | --- |
| Render/schema/Secret-reference/Kyverno and secret-scanning CI | Verified |
| Focused diagnostic/authentication suite | Verified, 67 tests |
| Public anonymous initialize, tools/list and tools/call | Verified, all 401 with the distinct resource challenge |
| Public DCR, exact callbacks, S256, cookie-bound consent and scoped Entra redirect | Verified |
| Live adapter reads with service-account impersonation | Verified, complete pagination over 178 pods and eight nodes; metrics, events, services/endpoints, storage and Flux |
| Explicit RBAC denials | Verified, Secrets/ConfigMaps, exec/attach/port-forward/proxy, token creation, mutations and watch |
| Finite fixture restarts, current-log redaction and Flux HealthCheckFailed | Verified using synthetic evidence only |
| Fixture previous-container log content | Unavailable from the runtime; kubelet returned a retrieval error despite HTTP 200. The adapter reports unavailable evidence rather than application logs |
| Encrypted client state after provider replacement | Verified by the persistence regression test |
| In-cluster and public-authentication Gatus checks | Verified healthy |
| Actual ChatGPT user sign-in/token exchange and signed-in ChatGPT diagnostic calls | Not run; connect the server and sign in using the setup above |

The temporary `kubernetes-mcp-validation` Flux entry is removed after the test,
allowing Flux to prune its failed Job. The independent two-minute log fixture
was also removed. The finite Job source in `infra/kubernetes-mcp/validation`
is retained as an opt-in drill, without a live reconciliation entry. Collect
logs during its first restart: Kubernetes can delete the retrying Pod when the
Job reaches its backoff limit. Neither the Job nor its log fixture mounts
credentials or contacts another service. No private production evidence is
included in this ledger.

References: [OpenAI MCP OAuth](https://developers.openai.com/plugins/build/auth),
[FastMCP Azure provider](https://gofastmcp.com/docs/integrations/azure),
[FastMCP OAuth proxy](https://gofastmcp.com/docs/servers/auth/oauth-proxy),
[Microsoft access-token claims](https://learn.microsoft.com/en-us/entra/identity-platform/claims-validation).
