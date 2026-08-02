# Controlled Longhorn replica relocation

This procedure reduces replica concentration without deleting a Replica or
evicting an entire node. It is deliberately one source Replica at a time and
uses Longhorn's supported `spec.evictionRequested` controller path: Longhorn
first creates and rebuilds a replacement, then removes only the requested
source Replica after the volume is healthy.

Do not use node-level eviction, delete a Replica, force-detach a volume, or
switch global auto-balance to `best-effort`. Those can affect more than one
volume at once.

## Preconditions

This is a storage maintenance operation and requires an explicit owner
approval for each batch. Start only when there is no active backup, snapshot,
upgrade, or unrelated rebuild.

```bash
export KUBECONFIG="$HOME/.config/talos/cooked-k8s/kubeconfig-entra"
cd ~/Documents/Git/homelab-infra

scripts/longhorn-replica-relocation-preflight.sh
```

The command must report `PASS`. It checks all Longhorn nodes and volumes, the
`least-effort` auto-balance setting, the rebuild limit of one, and that no
other eviction or rebuild is active. The targeted preflight below applies the
25% free-space reserve to the chosen destination disk.

Choose one source replica and one destination that does not already host that
volume. Keep `metal5` out of the destination set while its remaining capacity
is tight. Re-evaluate the target before every move; capacity and replica
placement are live state, not fixed inventory.

```bash
volume='pvc-REPLACE-WITH-LONGHORN-VOLUME-NAME'
source='metal6'
target='metal8'

scripts/longhorn-replica-relocation-preflight.sh \
  --volume "$volume" --source "$source" --target "$target"
```

The targeted preflight prints the exact source Replica name and refuses to
continue if the target lacks safe capacity, has existing tags, the Volume has
an existing node selector, a replica already exists on the target, or any
cluster-wide health condition is unsafe.

## Start exactly one controller-managed move

After the targeted preflight passes, substitute the reported source Replica
name. The temporary tag makes the destination deterministic. It is safe only
because the preflight asserted that both the target node tags and volume node
selector were empty.

```bash
replica='REPLACE-WITH-PREFLIGHT-SOURCE-REPLICA'
tag="replica-relocation-$(date -u +%Y%m%d%H%M%S)"

kubectl -n longhorn-system patch nodes.longhorn.io "$target" --type=merge \
  -p "{\"spec\":{\"tags\":[\"$tag\"]}}"

kubectl -n longhorn-system patch volumes.longhorn.io "$volume" --type=merge \
  -p "{\"spec\":{\"nodeSelector\":[\"$tag\"]}}"

kubectl -n longhorn-system patch replicas.longhorn.io "$replica" --type=merge \
  -p '{"spec":{"evictionRequested":true}}'
```

Longhorn's replica rebuild limit remains one. Do not issue another eviction
while this one is active. Monitor the specific volume in the Longhorn UI or
with Kubernetes until it is healthy and has exactly three running replicas:

```bash
kubectl -n longhorn-system get volumes.longhorn.io "$volume" \
  -o jsonpath='{.status.robustness}{" state="}{.status.state}{"\n"}'
kubectl -n longhorn-system get replicas.longhorn.io \
  -o custom-columns='NAME:.metadata.name,VOLUME:.spec.volumeName,NODE:.spec.nodeID,EVICT:.spec.evictionRequested,STATE:.status.currentState' \
  | grep "$volume"
```

Stop and investigate before the next move if the source Replica does not
disappear, the replacement lands outside the selected target, the volume is
not healthy, a target disk becomes unschedulable, or any unrelated rebuild
starts. Do not delete or force-detach anything to make progress.

## Clear temporary scheduling state

Only after the volume is healthy with exactly three running replicas and the
requested source Replica is gone, restore the temporary operational state:

```bash
kubectl -n longhorn-system patch volumes.longhorn.io "$volume" --type=merge \
  -p '{"spec":{"nodeSelector":[]}}'
kubectl -n longhorn-system patch nodes.longhorn.io "$target" --type=merge \
  -p '{"spec":{"tags":[]}}'

scripts/longhorn-replica-relocation-preflight.sh
```

Tags and the per-volume selector are intentionally transient and are not
declared by the GitOps node-disk manifests. Removing them after each verified
move prevents untracked placement policy from accumulating.
