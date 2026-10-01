# Remove an orphaned Longhorn VolumeAttachment

The rollout audit found an unattached VolumeAttachment stuck deleting since
December 2025. Its PV and Longhorn volume no longer exist; the external attacher
continues reporting PV-not-found errors while its finalizer remains.

Use `scripts/longhorn-remove-stale-attachment.py` only after reviewing the exact
attachment UID, expected PV/Longhorn volume name, and node. The default is a
dry run. The helper refuses a live attachment, an unexpected finalizer, an
existing PV or claim, any Longhorn volume/engine/replica reference, or a node
reporting the volume attached or in use. The apply operation tests the object
UID, resource version, deletion timestamp, spec, attached status and finalizer
before removing only the known external-attacher finalizer.

For the orphan found on 1 October 2026:

```sh
python3 scripts/longhorn-remove-stale-attachment.py \
  csi-b8bb23c7b51a7d1d982d3d3a0451a241f8d63971e2804626396694fe91bb38da \
  --uid e45799f1-ee6f-480b-99f8-2bb7107df347 \
  --volume pvc-e0e131ba-700b-4731-a916-bf45bc8c1dd6 --node metal7
```

After the guards pass, repeat with `--apply`. A resource version conflict stops
the operation; rerun the dry run against fresh state before retrying. Verify
the attachment is absent, every current PVC remains Bound, and current volume
health is unchanged. No PV, Longhorn volume, replica or data file is deleted.
