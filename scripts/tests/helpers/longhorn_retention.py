"""Longhorn retention fixtures with a client that rejects writes by default."""

import datetime as dt
import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location("retention", pathlib.Path(__file__).resolve().parents[2] / "longhorn-retire-backup-history.py")
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)

VOLUME = "pvc-11111111-1111-1111-1111-111111111111"

def fixtures():
    now = dt.datetime.now(dt.timezone.utc)
    def timestamp(offset): return (now - dt.timedelta(hours=offset)).isoformat().replace("+00:00", "Z")
    backup_volume = {"metadata": {"name": "backup-volume", "uid": "bv-uid"},
                     "spec": {"volumeName": VOLUME, "backupTargetName": "default"},
                     "status": {"lastBackupName": "newest"}}
    backups = []
    for name, age in [("old", 48), ("newest", 1)]:
        backups.append({"kind": "Backup", "metadata": {"name": name, "uid": name + "-uid", "resourceVersion": "1",
            "labels": {"backup-target": "default", "backup-volume": VOLUME},
            "ownerReferences": [{"kind": "BackupVolume", "name": "backup-volume", "uid": "bv-uid"}]},
            "status": {"state": "Completed", "progress": 100, "volumeName": VOLUME, "error": "",
                "backupCreatedAt": timestamp(age), "snapshotCreatedAt": timestamp(age),
                "url": retention.DESTINATION + "?backup=" + name + "&volume=" + VOLUME}})
    return backup_volume, backups

class FakeClient:
    def __init__(self):
        self.backup_volume, self.backups = fixtures()
        self.deleted = []
        image = "docker.io/longhornio/longhorn-engine:v1.11.1"
        self.volume = {"kind": "Volume", "metadata": {"name": VOLUME},
            "spec": {"dataEngine": "v1", "image": image, "numberOfReplicas": 3},
            "status": {"state": "attached", "robustness": "healthy", "currentImage": image, "lastBackup": "newest"}}
        self.engine = {"kind": "Engine", "metadata": {"name": "engine"}, "spec": {"volumeName": VOLUME, "active": True, "image": image},
            "status": {"currentState": "running", "currentImage": image, "replicaModeMap": {"r0": "RW", "r1": "RW", "r2": "RW"}}}
        self.replicas = [{"kind": "Replica", "metadata": {"name": "r" + str(i)},
            "spec": {"volumeName": VOLUME, "active": True, "nodeID": "node" + str(i), "healthyAt": "now", "failedAt": "", "image": image},
            "status": {"currentState": "running", "currentImage": image}} for i in range(3)]
        self.system = {"metadata": {"name": "system", "uid": "system-uid", "creationTimestamp": self.backups[-1]["status"]["backupCreatedAt"]},
            "spec": {"volumeBackupPolicy": "always"}, "status": {"state": "Ready", "version": "v1.11.1"}}
        self.image = image

    def csi_snapshot_contents(self):
        return []

    def request(self, path, body=None, missing_ok=False):
        if body is not None:
            self.deleted.append((path, body))
            raise AssertionError("Dry run must never issue deletion")
        return {"metadata": {"generation": 1}, "status": {"desiredNumberScheduled": 3,
            "numberReady": 3, "updatedNumberScheduled": 3, "observedGeneration": 1}}

    def get(self, resource, name=None, missing_ok=False):
        if resource == "settings": return {"value": "false" if name == "auto-cleanup-when-delete-backup" else self.image}
        if resource == "backuptargets": return {"spec": {"backupTargetURL": retention.DESTINATION, "credentialSecret": "longhorn-b2-credentials"}, "status": {"available": True}}
        if resource == "backupvolumes": return self.backup_volume if name else {"items": [self.backup_volume]}
        return {"items": {"systembackups": [self.system], "volumes": [self.volume], "engines": [self.engine],
            "replicas": self.replicas, "backups": self.backups}[resource]}
