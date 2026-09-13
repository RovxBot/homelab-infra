# Rotate the Gatus GitHub alert token

Gatus opens and resolves GitHub issues when a monitored endpoint fails or
recovers. Its token must be a narrowly scoped, short-lived fine-grained GitHub
personal access token (PAT). Never paste the token into chat, an issue, a pull
request, a shell command, or a GitHub Actions secret.

## Create the replacement token

In GitHub, go to **Settings → Developer settings → Personal access tokens →
Fine-grained tokens** and create a token with:

- Name: `gatus-homelab-infra-issues`
- Resource owner: `RovxBot`
- Repository access: **Only select repositories** → `homelab-infra`
- Repository permissions: **Issues: Read and write** only
- Expiration: 90 days (or another recorded rotation period)

GitHub metadata access is implicit. Do not grant Contents, Actions,
Administration, Pull requests, organization permissions, or any classic PAT
scope. Save the token and its expiry in Bitwarden before leaving the creation
page. Keep the old token valid until the replacement is verified.

## Encrypt the replacement locally

The repository SOPS rule contains the public age recipient needed to encrypt a
new Secret. A local age private key is not needed; only Flux in the cluster
can decrypt the result.

```bash
sudo pacman -S --needed sops
sops --version

cd ~/Documents/Git/homelab-infra
scripts/rotate-gatus-github-token.sh
git diff --check
git diff -- secrets/gatus-github-token.enc.yaml
```

The helper reads the token with hidden terminal input, streams it directly to
SOPS, and atomically replaces the encrypted file. It never writes plaintext to
disk. The diff must contain only `ENC[...]` ciphertext and SOPS metadata. Stop
immediately if a token value is visible.

## Deploy and verify

The encrypted Secret must be committed together with a non-secret pod-template
annotation bump in `infra/observability/gatus-helmrelease.yaml`. Gatus reads
the token as an environment variable, so a Secret update alone does not update
the running Pod.

After the pull request is merged:

```bash
export KUBECONFIG="$HOME/.config/talos/cooked-k8s/kubeconfig-entra"

kubectl -n flux-system annotate kustomization/infrastructure \
  reconcile.fluxcd.io/requestedAt="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --overwrite
kubectl -n flux-system wait --for=condition=ready kustomization/infrastructure --timeout=5m
kubectl -n monitoring rollout status deployment/gatus --timeout=5m
kubectl -n monitoring get deployment gatus \
  -o jsonpath='{.spec.template.metadata.annotations.homelab\.cooked\.beer/gatus-github-token-revision}{"\n"}'
kubectl -n monitoring logs deployment/gatus --since=10m | grep -Ei 'github|token|auth' || true
```

Verify that Gatus is Ready and logs show no GitHub authentication failure. Do
not display the Secret data. A synthetic alert that creates a test GitHub issue
needs an explicit owner decision; otherwise use the next genuine alert as the
positive write test.

Only after verification, revoke the old token in GitHub and record the new
expiry in Bitwarden. Before revocation, rollback is simply reverting the
rotation pull request and reconciling Flux.
