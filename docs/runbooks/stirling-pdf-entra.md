# Stirling PDF with Entra sign-in

Open **https://stirling.cooked.beer** from any desktop browser and sign in
with the assigned Entra account. Homepage also links to the service.

## Accounts and access

Only `sam@cooked.beer` and `rachael@cooked.beer` are assigned access. Sam is
the native Stirling administrator and workspace owner; Rachael is a standard
user. Two users fit within Stirling's free five-user allowance. Native login
provides individual accounts and private document libraries. Paid features
remain subject to upstream licensing.

Entra's single-tenant enterprise application requires user assignment. Its
assigned security group has explicit membership, rather than a rule admitting
every cooked.beer account. Removing a member prevents future Entra sign-ins;
also disable their native Stirling account to revoke application access and
review any API keys. Existing sessions are not necessarily terminated by an
Entra membership change alone. Existing tenant sign-in policies govern MFA.

| Identity setting | Value |
| --- | --- |
| Tenant | `345cd18b-5bee-4ac4-b729-a3f7059a28f3` |
| Entra app | `Stirling PDF OIDC` |
| Application/client ID | `cd907a08-745a-4015-93c7-0a1bbd10db96` |
| Access group | `Stirling PDF Users` |
| Group ID | `4ae02281-2f44-4703-b531-87bb081fead6` |
| Redirect URI (Web) | `https://stirling.cooked.beer/login/oauth2/code/entra` |
| Username claim | `preferred_username` |
| Requested scopes | `openid profile email` |
| Client secret expires | 16 October 2027, Adelaide time |

Native OAuth2 login is required on routes that handle documents; the backend
enforces authentication even if Cilium policy enforcement is disabled.
Password sign-in is disabled. The shared OAuth2 Proxy is removed. Native
Stirling API keys authenticate API requests directly and should be treated
as account credentials. Scanner and signature links retain upstream access
semantics; avoid distributing document links beyond their intended recipients.

Account creation is temporarily enabled during migration, while Entra
assignment limits admission to these two people. Once both accounts exist,
set `SECURITY_OAUTH2_AUTOCREATEUSER=false` and
`SECURITY_OAUTH2_BLOCKREGISTRATION=true`. To add a future user, create their SSO
account in Stirling and add them to the assigned Entra group, checking the
five-user allowance first. Set roles in Stirling; Entra membership alone
does not grant administrator privileges.

## PDF tools and limits

The pinned full `3.2.0-fat` image supplies LibreOffice, Tesseract/OCRmyPDF,
Calibre, WeasyPrint, Ghostscript, QPDF, and ImageMagick. Core PDF editing,
merging, splitting, redaction, compression, signatures, OCR, and supported
Office, image, HTML, and ebook conversions run on the homelab. AI is disabled
by request; document chat needs a model endpoint or provider credentials.

Files are capped at 95 MB and multipart requests at 100 MB. Cloudflare's
request-duration limits can interrupt long OCR or conversion requests; use
smaller batches. PDF-to-CBR requires the separately licensed `rar` executable,
which the official image does not ship. Paid integrations and enterprise
controls need the corresponding Stirling licence. The generated cooked.beer
server signing certificate is self-signed, not publicly trusted.

Maintain deployment settings through `apps/stirling-pdf/settings.env` in Git.
Environment variables override settings saved by administrators in the UI.

## Storage and health

One 20 GiB Longhorn PVC persists `/configs`, `/storage`, `/customFiles`,
`/pipeline`, and `/logs`. Retain `/configs` alongside documents: it holds the
database, account records, and signing keys. Namespace and PVC pruning is
disabled to retain data during rollback. Never delete the namespace as a
rollback procedure. Existing weekly Longhorn backups use
`volume-backup-policy: always`; backup schedules and targets are unchanged.

The single replica uses Recreate for its RWO volume, so rollouts briefly
interrupt service. The application runs without root or capabilities, with
a read-only container filesystem and the required LibreOffice Landlock
sandbox. Temporary processing files occupy a bounded ephemeral volume.

```bash
kubectl -n flux-system get kustomization apps-stirling-pdf
kubectl -n stirling-pdf get pods,pvc,endpointslices
kubectl -n stirling-pdf rollout status deployment/stirling-pdf --timeout=15m
kubectl -n stirling-pdf logs deployment/stirling-pdf -c stirling-pdf --since=10m
```

Gatus expects public `/api/v1/info/status` to return 200 and unauthenticated
`/api/v1/auth/me` to return 401. Kubernetes probes use the same status endpoint
over loopback. No document-processing API is exempted from authentication.

## Rollout, account recovery, and rollback

Render Kustomize, check schemas and Secret references, run the Kyverno baseline
gate and server-side dry run, then merge a focused PR after CI passes. Flux
reconciles `main`. The full image's first pull can take several minutes.
Cloudflare routes `stirling.cooked.beer` to the Stirling Service on port 80;
only the authenticated native application is exposed on container port 8080.

Before native-auth migration, Longhorn snapshot
`stirling-before-native-entra-20261016` was taken for volume
`pvc-a7bfc77f-42e8-4d15-93dc-f77f4b6d8836`. The existing database is retained.
Upstream's `STIRLING_ORG_OWNER_BREAK_GLASS` can recover ownership to an existing
native user: temporarily set it to that user's exact username and restart.
It promotes the selected user and transfers ownership at startup. Verify the
new owner, then remove the variable immediately and restart through GitOps.
Do not leave ownership recovery enabled during normal operation. Remove the
unused bootstrap `admin` account after ownership has been transferred.

Verify fresh Entra sign-in, Sam's administrator/owner status, Rachael's standard
SSO account, rejection of unauthenticated document APIs, healthy monitoring,
and merge/split, image conversion, Office-to-PDF, and OCR. Record separately
whether each user has actually completed a browser login.

For failures, revert the affected Git change while retaining the namespace,
PVC, encrypted Secret, and identity resources. Never disable authentication
to work around an OIDC problem. A schema-changing upgrade may require the
matching database snapshot as well as an image rollback.

## Secret rotation

`secrets/stirling-pdf-oidc.enc.yaml` supplies `SECURITY_OAUTH2_CLIENTID` and
`SECURITY_OAUTH2_CLIENTSECRET` to the native application. Create a replacement
Entra app secret before expiry, update its SOPS ciphertext, and roll out a
pod-template change through Git. Updating a Secret alone does not restart
containers. Verify fresh SSO before revoking the previous credential. Never
print decrypted credentials or commit plaintext secrets.

## Upstream references

- [Docker features and persistent paths](https://docs.stirlingpdf.com/Server/Install/Docker/)
- [Native OAuth2/OIDC configuration](https://docs.stirlingpdf.com/Server/Security/OAuth-SSO/)
- [Sign-in and security](https://docs.stirlingpdf.com/Server/Security/Sign-in-and-Security/)
- [Plans and licences](https://docs.stirlingpdf.com/Server/Licensing/Plans-and-Licences/)
- [Entra application user assignments](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/assign-user-or-group-access-portal)
