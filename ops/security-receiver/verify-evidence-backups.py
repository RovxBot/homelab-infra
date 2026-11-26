"""Read-only acceptance of the two exact reviewed evidence backup points."""
import json
import subprocess

EXPECTED = {
    "security-collector-state": (
        "pvc-28c469e0-c283-4576-ad49-1e8dc0c9384b", "security-collector-retirement-20261126"),
    "security-receiver-state": (
        "pvc-d431a342-8db1-435b-b899-a501908b7873", "security-receiver-retirement-20261126"),
}


def get(namespace, resource, name):
    return json.loads(subprocess.check_output(
        ["kubectl", "-n", namespace, "get", resource, name, "-o", "json"], text=True))


def verify(read=get):
    results = {}
    for claim, (volume, name) in EXPECTED.items():
        pvc = read("kubescape", "pvc", claim)
        v = read("longhorn-system", "volumes.longhorn.io", volume)
        snap = read("longhorn-system", "snapshots.longhorn.io", name)
        backup = read("longhorn-system", "backups.longhorn.io", name)
        s, b = snap["status"], backup["status"]
        results[claim] = bool(
            pvc["status"]["phase"] == "Bound" and pvc["spec"]["volumeName"] == volume
            and v["status"]["robustness"] == "healthy" and v["spec"]["numberOfReplicas"] == 3
            and snap["spec"]["volume"] == volume and s.get("readyToUse") is True
            and not s.get("error") and backup["spec"]["snapshotName"] == name
            and b.get("volumeName") == volume and b.get("snapshotName") == name
            and b.get("state") == "Completed" and b.get("progress") == 100
            and not b.get("error") and bool(b.get("snapshotCreatedAt"))
            and bool(b.get("url")))
    return results


if __name__ == "__main__":
    try:
        result = verify()
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError):
        print("security_evidence_backup_verification_unavailable")
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if all(result.values()) else 1)
