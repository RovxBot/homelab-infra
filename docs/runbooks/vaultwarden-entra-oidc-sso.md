# Vaultwarden Entra OIDC SSO

This runbook moves the existing `sam@cooked.beer` Vaultwarden account to
native Entra OpenID Connect (OIDC) sign-in. It deliberately uses Vaultwarden's
native SSO implementation, not Cloudflare Access in front of the service:
Cloudflare Access would gate the web UI but can break Bitwarden browser,
desktop, and mobile API clients, and it would not associate the Entra identity
with the Vaultwarden account.

Vaultwarden's SSO implementation links an existing account using its exact
email address and then stores the issuer/subject association separately. The
existing Vaultwarden user UUID and encrypted vault are retained.

## Important limitations

- Entra handles the sign-in challenge and any Conditional Access/MFA policy.
- The current Vaultwarden master password remains necessary to decrypt the
  existing vault. It is a cryptographic key and is not replaced by an Entra
  password or MFA factor.
- Vaultwarden's own two-factor login is a separate second factor. Current
  Vaultwarden does not treat an Entra MFA claim as satisfying that factor. Do
  not remove it until native SSO and the Entra Conditional Access policy have
  been tested end-to-end.
- This requires Vaultwarden `1.36.0` or newer. The deployed `1.37.3` image
  meets that requirement.

## 1. Create the Entra application

In **Entra ID > App registrations**, register **Vaultwarden OIDC** with these
settings:

- **Supported account types:** Accounts in this organizational directory only
  (single tenant).
- **Platform:** Web.
- **Redirect URI:**
  `https://vault.cooked.beer/identity/connect/oidc-signin`
- **Client secret:** create a dedicated secret and record its expiry in the
  password-manager item for this application. Use the *secret value*, not its
  ID.
- **Token configuration:** add the optional `email` claim to the **ID token**.
  Confirm its value is exactly `sam@cooked.beer`.
- **API permissions:** retain/add delegated Microsoft Graph `User.Read`.
  Admin consent is not required for the normal delegated permission in a
  single-user tenant, unless the tenant's consent policy requires it.

Record only these values for the Kubernetes secret:

- Directory (tenant) ID
- Application (client) ID
- Client secret *value*

`preferred_username` is emitted with the `profile` scope and supplies the
Vaultwarden display name. Entra normally does not emit `email_verified`; the
first sign-in therefore needs the short, controlled association window in the
next section.

## 2. Stage the account association

The OIDC settings belong in the dedicated encrypted
`secrets/vaultwarden-sso.enc.yaml` Secret. Do not add them to the existing
Vaultwarden Secret, and do not create a plaintext Secret. Create or update it
through the approved SOPS workflow with access to its age identity. Keep the
initial association configuration exactly as shown:

```yaml
SSO_ENABLED: "true"
SSO_ONLY: "false"
SSO_SIGNUPS_ALLOWED: "false"
SSO_SIGNUPS_MATCH_EMAIL: "true"
SSO_ALLOW_UNKNOWN_EMAIL_VERIFICATION: "true"
SSO_AUTHORITY: "https://login.microsoftonline.com/<DIRECTORY_TENANT_ID>/v2.0"
SSO_SCOPES: "openid profile offline_access User.Read"
SSO_CLIENT_ID: "<APPLICATION_CLIENT_ID>"
SSO_CLIENT_SECRET: "<CLIENT_SECRET_VALUE>"
```

`SSO_ONLY` stays false during this test, preserving the normal password-login
path as a rollback route. SSO-created accounts are disabled; the only allowed
association is the already existing account with the exact matching email.
`SSO_ALLOW_UNKNOWN_EMAIL_VERIFICATION` is needed only because Entra does not
usually provide the `email_verified` claim. It is safe to remove after the
association is complete.

Commit the encrypted file and let Flux reconcile it. Check the deployment and
recent server logs without exposing secret values:

```bash
export KUBECONFIG="$HOME/.config/talos/cooked-k8s/kubeconfig-entra"
kubectl -n security rollout status deployment/vaultwarden --timeout=5m
kubectl -n security logs deployment/vaultwarden --since=10m
```

## 3. Verify before enforcing SSO

Use the public Vaultwarden URL, not a NodePort URL, and select **Log in with
SSO**. Complete the Entra sign-in and MFA prompt, then enter the existing
Vaultwarden master password to unlock the encrypted vault.

Verify all of the following before continuing:

- The displayed account and vault contents are the existing `sam@cooked.beer`
  account; do not continue if a new empty account is shown.
- The Entra sign-in required the intended Conditional Access/MFA controls.
- A browser extension, desktop client, and mobile client can complete their
  respective browser redirect and sync. Keep the existing signed-in client as
  a recovery device until every client is confirmed.
- Vaultwarden logs show a successful login and no OIDC discovery, redirect,
  token, or email-association error.

If the existing account is not linked, stop. Do not create another Vaultwarden
account or change its email. First confirm the Entra ID-token `email` claim is
exactly `sam@cooked.beer`.

## 4. Enforce Entra sign-in and MFA

After a successful association, update the same encrypted SSO Secret again:

```yaml
SSO_ONLY: "true"
SSO_SIGNUPS_MATCH_EMAIL: "false"
SSO_ALLOW_UNKNOWN_EMAIL_VERIFICATION: "false"
```

This disables email-and-master-password login, prevents any later email-based
association, and closes the temporary unknown-email-verification window. The
already linked account continues to use its issuer/subject mapping.

In **Entra ID > Protection > Conditional Access**, create a policy targeting
the **Vaultwarden OIDC** enterprise application and the intended user(s). Start
it in report-only mode, inspect the sign-in logs, then require MFA and enable
the policy. Exclude a documented Entra emergency-access account from every
Conditional Access policy; keep its credentials in a separately protected
location. Do not apply a broad tenant-wide policy as part of this rollout.

Use a sign-in frequency appropriate to the password-manager risk and verify it
with a desktop and mobile client. Conditional Access becomes the source of
Entra MFA prompts; Vaultwarden's own two-factor setting remains independent.

## Rollback

If Entra, OIDC discovery, or a client redirect fails, set `SSO_ONLY: "false"`
in the encrypted Secret, commit, and wait for the Vaultwarden rollout. The
account, encrypted vault data, and SSO association are not deleted by this
rollback.

Do not delete the Entra app registration or run database cleanup while
investigating an issue. Deleting the `sso_users` association is a recovery
operation and should be done only after a verified backup and a deliberate
decision to re-link the identity.

## Secret rotation

Before the Entra app secret expires, create a replacement secret, update only
`SSO_CLIENT_SECRET` through SOPS, commit, reconcile, and test a fresh SSO
login. Revoke the previous Entra secret only after the new one works.
