# OpenBrew with Entra sign-in

Open **https://openbrew.cooked.beer** and sign in as `sam@cooked.beer`.
Homepage also links to OpenBrew. The local account is `sam`; no separate
OpenBrew password is needed.

## Identity and authentication

| Setting | Value |
| --- | --- |
| Tenant | `345cd18b-5bee-4ac4-b729-a3f7059a28f3` |
| Entra application | `OpenBrew OIDC` |
| Client ID | `f74ad272-7413-473f-ac37-0c571687e978` |
| Application object | `b7ad9162-7ced-4d04-9ba0-0956bd281b02` |
| Enterprise application object | `3b7e0a20-13ec-4ede-80d8-a706d8edf6b6` |
| Redirect URI (Web) | `https://openbrew.cooked.beer/oauth2/callback` |
| Assigned user object | `444d9945-1578-4993-b9f4-c5d300886006` (Sam) |
| Delegated scopes | `openid profile email`, consented only for Sam |
| Client secret expires | 31 October 2027, 10:30 am Adelaide time |

The single-tenant enterprise application requires assignment and assigns its
`OpenBrew.User` role directly to Sam. Existing tenant sign-in/MFA policies
apply. There is no tenant-wide group or domain-wide access grant.

Upstream supports Google and password login, without native Entra OIDC.
OAuth2 Proxy validates Entra issuer, audience, signature, nonce, and PKCE,
then checks an exact email allowlist. Its private Basic credential and
validated email are checked by `EntraSso.cs` on every application request.
The adapter sets the native user identity to Sam's persisted OpenBrew user ID.
Existing application cookies cannot replace the proxy identity. Local
password, Google, registration and reset routes are disabled by the adapter.

Only OAuth2 Proxy is exposed by the Service. XSP binds `127.0.0.1:8085` in
the same pod. The application requires the shared proxy credential even on
loopback; `/healthz` returns only a database/account health result for local
container probes. No application data route bypasses sign-in. NetworkPolicy
resources document intended ingress; Cilium enforcement is currently disabled.

The adapter is compiled during initialization against the pinned upstream
Mono image. The runtime copies the web root to a bounded `emptyDir`, adds
the module to the classic ASP.NET `httpModules`, and disables Forms login.
This preserves the upstream application and avoids maintaining a fork/image.
Review and rerun adapter and authenticated lifecycle checks before changing
the upstream image. Upstream source:
<https://github.com/aderbique/openbrew/tree/024137b34f88eb1a5d24fb34b247dfb6408d4cf7>.

OAuth sessions expire after at most eight hours and may prompt earlier when
Entra tokens expire. Removing the Entra assignment prevents future sign-ins;
to revoke existing proxy sessions immediately, rotate the cookie secret in
the SOPS Secret and roll out the web deployment. Logout clears the proxy
cookie and opens the tenant's logout endpoint.

## Database, uploads, and operation

SQL Server 2022 Express runs on a dedicated 20 GiB Longhorn PVC. Express is
limited to 10 GB per database. Recipe photos use a separate 5 GiB PVC mounted
at `/runtime/Media`, matching the directory XSP serves. Namespace and PVC
pruning are disabled. Preserve both volumes during rollback or recovery.
The existing Longhorn system-backup policy remains responsible for volume
backups; do not treat a running pod or replicated volume as proof of a
verified off-host recovery point. SQL Server recovery after a volume restore
must be tested before depending on it for important recipes.

Local DNS blocks `mcr.microsoft.com`, including node image pulls. The
`OpenBrew database image` workflow mirrors the exact Microsoft image to
`ghcr.io/rovxbot/openbrew-sqlserver` with its original digest preserved.
The package is public and contains only Microsoft's unmodified image.
Before updating SQL Server, update the workflow source/tag, run the mirror,
then pin the matching digest in the database Deployment. No cluster DNS or
registry configuration change is required.

Only the initialization container receives the SQL administrator password.
It runs upstream's idempotent schema/catalog migrations, creates Sam's local
account if missing, and provisions a separate `openbrew` SQL login limited
to database data-reader/data-writer roles and EXECUTE. The web process uses
that restricted login. Secret values are committed only as SOPS ciphertext.

The web application and database each use one replica with Recreate because
their volumes are RWO. Upgrades briefly interrupt availability. Containers
run without root or a service-account token and use read-only root filesystems
with explicit writable volumes. All capabilities are dropped; SQL Server adds
only `NET_BIND_SERVICE`, which Microsoft's executable declares as a required
file capability. This is permitted by the restricted Pod Security Standard.

SMTP and Google credentials are intentionally unconfigured. Personal recipe
and brew-session workflows use Entra; email-based contact/newsletter/reset
features require SMTP if wanted later. Turnstile is disabled for this private,
authenticated instance; registration itself is blocked.

```bash
kubectl -n flux-system get kustomization apps-openbrew
kubectl -n openbrew get pods,pvc,endpointslices
kubectl -n openbrew logs deployment/openbrew -c prepare-runtime
kubectl -n openbrew logs deployment/openbrew -c openbrew --since=10m
kubectl -n openbrew logs deployment/openbrew -c oauth2-proxy --since=10m
```

Gatus checks proxy readiness and expects unauthenticated `/oauth2/auth` to
return 401. Web readiness separately checks the database and active account.

## Rollout and rollback

Render Kustomize, validate schemas/Secret references, run the Kyverno gate,
compile and test the adapter on the pinned image, and run admission dry runs.
Merge the focused PR after checks pass; Flux reconciles `main`. Add only the
proxied `openbrew.cooked.beer` CNAME pointing to
`19dd24d6-5e63-4ba3-a23c-fbebc7312e3f.cfargotunnel.com` in Cloudflare DNS.

Validate fresh Entra login, the native `sam` identity, recipe creation/save,
unauthenticated denial, and both volumes' health. If readiness or sign-in
fails, keep the route behind authentication and fix or revert the application
change through Git. For a full removal, revert the hostname/Homepage/Gatus
and Flux wiring in Git. Retained namespace/PVC resources must not be deleted
as part of rollback. Do not remove Entra registration or stored recipe data
while diagnosing authentication.

Before the Entra client secret expires, add a replacement credential, update
only `OAUTH2_PROXY_CLIENT_SECRET` through SOPS, roll out the web deployment,
and test a fresh login before revoking the old credential. Changing the SQL
runtime password requires updating the SQL login and its encrypted connection
string together; initialization deliberately preserves an existing login.
