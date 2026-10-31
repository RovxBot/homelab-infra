# Entra sign-in

The `Cooked Gitea` application is single tenant
`345cd18b-5bee-4ac4-b729-a3f7059a28f3`. Application ID:
`9f785317-173f-45c7-bc2e-e15f655d515d`; object ID:
`9939273c-eace-47d7-b593-a73df82834c0`.

The enterprise application requires assignment. Sam is initially assigned
`Gitea.Admin`; additional developers must be explicitly assigned `Gitea.User`
or the administrator role. Assignment can use a dedicated Entra security group
as membership grows. The OIDC `roles` claim maps only `Gitea.Admin` to Gitea
administrator. No tenant-wide administrator grant exists. No application API
permissions or client-credential Graph access are needed.

The `entra` authentication source uses tenant-specific OIDC discovery, scopes
`openid,profile,email` and Authorization Code flow. Its Web callback is
`https://git.cooked.beer/user/oauth2/entra/callback`. Implicit flows are disabled.
The installed Gitea CLI supports `group-claim-name` and `admin-group`.

Flux supplies the key/secret from `secrets/gitea-entra.enc.yaml`. New assigned
identities can register through OIDC; self-registration by password is disabled.
Existing accounts require authenticated linking (`ACCOUNT_LINKING=login`),
avoiding automatic ownership transfer based only on an email match.

Keep the separate `recovery-admin` account in `gitea-admin.enc.yaml`. Its password
is installed only initially; credential recovery/rotation is an explicit action.
Do not link this account to Entra or disable local login. Recover it over a local
port-forward if DNS/Entra is down. Keep the encrypted Secret and its age identity
in the independent encrypted recovery repository.

Provision/reconcile with the intended administrator logged in to Azure CLI:

```bash
python3 ops/gitea/configure-entra.py --sops /path/to/sops
```

The script refuses ambiguous applications, preserves existing credentials,
requires enterprise-app assignment, and assigns only the signed-in operator as
initial admin. It never prints the client secret. For rotation, append
`--rotate`, commit the resulting SOPS change and reconcile. Restart the Gitea
deployment through a Helm values annotation change so the init container
updates the authentication source, then test a new browser login before
removing the old Entra password credential by its key ID. Initial credential
expiry is 31 October 2027; alert/rotate at least 30 days beforehand.

Acceptance requires a real browser login as Sam, an unassigned-user denial,
administrator role verification, account linking and local recovery login.
A successful redirect/discovery request alone does not prove SSO works.
