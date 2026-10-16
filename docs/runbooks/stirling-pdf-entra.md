# Stirling PDF browser workspace

Open **https://stirling.cooked.beer** from any desktop browser and sign in with an
enabled `@cooked.beer` Entra member account. The Homepage dashboard also links
to Stirling PDF. No desktop installation is required.

## Access and sharing

This is the requested free deployment: Entra OIDC at OAuth2 Proxy, with native
Stirling login disabled. All admitted users share one workspace. Saved server
files, pipeline definitions, and signing assets are shared; this configuration
does not provide private per-user libraries, individual Stirling roles, or
per-user audit history. Download sensitive results and remove saved documents
when finished. Disabling native login does not unlock paid Stirling features.

The access boundary has three parts:

1. The single-tenant Entra enterprise application requires assignment to the
   dedicated dynamic group.
2. OAuth2 Proxy verifies the tenant's signed ID token and requires that group's
   ID in the token, with a cooked.beer domain check as an additional filter.
3. Stirling binds to `127.0.0.1:8080` inside the same pod as OAuth2 Proxy. Only
   port 4180 on the gateway is served by Kubernetes and Cloudflare Tunnel.
   Direct pod-IP access to port 8080 is unavailable even while Cilium network
   policy enforcement is disabled. Do not change `SERVER_ADDRESS` to a public
   bind address while native login is disabled.

| Identity setting | Value |
| --- | --- |
| Tenant | `345cd18b-5bee-4ac4-b729-a3f7059a28f3` |
| Entra app | `Stirling PDF Gateway OIDC` |
| Application/client ID | `cd907a08-745a-4015-93c7-0a1bbd10db96` |
| Access group | `Stirling PDF - cooked.beer users` |
| Group ID | `502f5a1c-e46a-4105-9859-ab174615231e` |
| Redirect URI (Web) | `https://stirling.cooked.beer/oauth2/callback` |
| Group claim | `ApplicationGroup` (only groups assigned to this app) |
| Consented scopes | `openid profile email offline_access` |
| Client secret expires | 16 October 2027, Adelaide time |

The dynamic membership rule is:

```text
(user.accountEnabled -eq true) -and (user.userType -eq "Member") -and (user.userPrincipalName -endsWith "@cooked.beer")
```

Current and future matching accounts receive access after Entra finishes
processing membership. Guests, disabled accounts, and other domains are
excluded. Dynamic groups use the tenant's Entra P1 licensing. No tenant-wide
Conditional Access changes are part of this installation; existing applicable
sign-in policies remain responsible for MFA.

Gateway sessions last eight hours and refresh every 30 minutes. Membership
changes and account disablement are not instantaneous revocation of a valid
browser session. To terminate every existing gateway session urgently, rotate
the cookie secret and restart the deployment through GitOps.

## PDF tools and limits

The pinned full `3.2.0-fat` image supplies LibreOffice, Tesseract/OCRmyPDF,
Calibre, WeasyPrint, Ghostscript, QPDF, and ImageMagick. Core editing, merging,
splitting, redaction, compression, signatures, OCR, and supported Office,
image, HTML, and ebook conversions run on the homelab. The bundled AI engine
is off by user choice; AI chat needs a model endpoint or provider credentials.

All HTTP paths, including APIs, scanner/signature uploads, and downloads,
require the gateway session. A Stirling API key alone cannot bypass Entra.
Phones opening scanner/signature links must also sign in with an allowed
Entra account. Provider tokens are not forwarded to Stirling.

The public Cloudflare route has plan-dependent upload and request-duration
limits. Individual files are capped at 95 MB and multipart requests at 100 MB;
long OCR or conversion requests may hit Cloudflare's timeout even though the
gateway allows ten minutes. Prefer smaller batches. PDF-to-CBR additionally
needs the separately licensed `rar` binary, which the official image does not
ship. Paid integrations, enterprise controls, and external databases need an
appropriate Stirling licence and are not enabled by this free installation.

The shared UI cannot change server settings (`SYSTEM_SHOWSETTINGSWHENNOLOGIN`
is false). Maintain server configuration through Git, including settings in
`apps/stirling-pdf/settings.env`. Signing with the generated cooked.beer server
certificate is self-signed, not a publicly trusted or qualified signature.

## Storage and health

One 20 GiB Longhorn PVC persists `/configs`, `/storage`, `/customFiles`,
`/pipeline`, and `/logs`. Keep `/configs` with the files: it contains database
state and signing keys. The namespace and PVC have Flux pruning disabled to
preserve data during rollback; deleting the namespace still deletes its PVC, so do not use
namespace deletion as rollback. Existing weekly Longhorn system backups use
`volume-backup-policy: always`; no backup schedules or B2 targets are changed.

The workload uses one replica and the Recreate strategy for its RWO volume.
Upgrades cause a brief outage. The backend requires the upstream LibreOffice
Landlock sandbox, runs without root/capabilities, and has a read-only container
filesystem. Temporary processing files are bounded in an ephemeral volume.

```bash
kubectl -n flux-system get kustomization apps-stirling-pdf
kubectl -n stirling-pdf get pods,pvc,endpointslices
kubectl -n stirling-pdf rollout status deployment/stirling-pdf --timeout=15m
kubectl -n stirling-pdf logs deployment/stirling-pdf -c oauth2-proxy --since=10m
kubectl -n stirling-pdf logs deployment/stirling-pdf -c stirling-pdf --since=10m
```

Gatus checks gateway readiness and requires unauthenticated `/oauth2/auth` to
return 401. The Service has endpoints only while both containers are ready.
Application probes run on loopback; there is no unauthenticated public PDF
health/API exception. Gateway access logging is disabled to avoid recording
document names, query strings, and authorization callback codes. Jetty accepts
up to 32 KiB of request headers to accommodate encrypted Entra session cookies.

## Rollout and rollback

Before rollout, check Flux, node capacity, and Longhorn health. Render the new
Kustomization, validate its schemas and Secret references, run the Kyverno
baseline gate and a server-side dry run, then merge the reviewed PR after CI
passes. Flux reconciles `main`. The first pull of the full image can be slow.

The only Cloudflare DNS addition is a proxied CNAME for `stirling.cooked.beer` to
`19dd24d6-5e63-4ba3-a23c-fbebc7312e3f.cfargotunnel.com`. The tunnel routes that
hostname to the gateway Service. No other DNS or Access applications change.

Verify fresh Entra sign-in, PDF merge/split, image conversion, Office-to-PDF,
OCR, and that an unauthenticated PDF API request redirects to Entra. Verify a
guest/nonmember cannot obtain access and pod-IP port 8080 refuses connections.
Do not call the installation fully verified until a browser completes the
provider callback and returns to Stirling.

If sign-in or PDF processing fails, revert the app, Homepage, monitoring, and
tunnel changes in Git and reconcile. Retain the namespace, PVC, encrypted
Secret, and Entra resources while investigating. Remove just the PDF CNAME if
the new public route must be withdrawn. Never expose the backend or turn off
gateway authentication to work around an OIDC failure. Before a schema-changing
upgrade, take a Longhorn snapshot/backup; image rollback may need the matching
database snapshot.

## Secret rotation

The dedicated SOPS Secret is `secrets/stirling-pdf-oidc.enc.yaml`, consumed only
by OAuth2 Proxy. It contains `OAUTH2_PROXY_CLIENT_ID`,
`OAUTH2_PROXY_CLIENT_SECRET`, and `OAUTH2_PROXY_COOKIE_SECRET`. Create a new app
secret before expiry, update the SOPS ciphertext, and trigger a deployment
rollout through Git (for example, a pod-template rotation annotation). Secret
environment changes alone do not restart running containers. Test fresh SSO
before revoking the old app secret. Never print the decrypted Secret or commit
plaintext credentials. Cookie-secret rotation signs everyone out.

## Upstream references

- [Docker image features and persistent paths](https://docs.stirlingpdf.com/Server/Install/Docker/)
- [Stirling plans and licences](https://docs.stirlingpdf.com/Server/Licensing/Plans-and-Licences/)
- [OAuth2 Proxy Microsoft Entra configuration](https://oauth2-proxy.github.io/oauth2-proxy/configuration/providers/ms_entra_id/)
- [Entra dynamic membership rules](https://learn.microsoft.com/en-us/entra/identity/users/groups-dynamic-membership)
