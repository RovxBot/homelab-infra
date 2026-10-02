import copy
import datetime as dt
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("refresh", Path(__file__).resolve().parents[1] / "longhorn-refresh-share-manager.py")
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)
TARGET = "docker.io/longhornio/longhorn-share-manager:v1.13.0"


def export():
    volume = {"metadata": {"name": "test", "uid": "volume-original"},
              "spec": {"accessMode": "rwx"}, "status": {"shareEndpoint": "nfs://stable/test"}}
    manager = {"metadata": {"name": "test", "uid": "manager-original"},
               "spec": {"image": TARGET}, "status": {"state": "running", "endpoint": "nfs://stable/test"}}
    pod = {"metadata": {"name": "share-manager-test", "uid": "pod-original", "resourceVersion": "12",
                        "ownerReferences": [{"kind": "ShareManager", "uid": "manager-original", "controller": True}]},
           "spec": {"containers": [{"image": "longhornio/longhorn-share-manager:v1.11.1"}]},
           "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]}}
    return volume, manager, pod


def clients():
    pv = {"kind": "PersistentVolume", "metadata": {"name": "original-pv", "uid": "pv-uid"},
          "spec": {"csi": {"driver": "driver.longhorn.io", "volumeHandle": "test"},
                   "claimRef": {"namespace": "app", "name": "data", "uid": "claim-uid"}}, "status": {"phase": "Bound"}}
    pvc = {"kind": "PersistentVolumeClaim", "metadata": {"namespace": "app", "name": "data", "uid": "claim-uid"},
           "spec": {"volumeName": "original-pv"}, "status": {"phase": "Bound"}}
    pod = {"kind": "Pod", "metadata": {"namespace": "app", "name": "consumer", "uid": "client-uid"},
           "spec": {"volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "data"}}],
                    "containers": [{"name": "app", "volumeMounts": [{"name": "data", "mountPath": "/data"}]}]},
           "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]}}
    return [pv, pvc, pod]


class ShareManagerGuards(unittest.TestCase):
    def test_missing_or_non_nfs_client_mount_blocks_refresh(self):
        volume, _, _ = export()
        with patch.object(refresh, "run", return_value="ext2/ext3\n"):
            with self.assertRaises(refresh.UnsafeRefresh):
                refresh.check_nfs_mounts(volume, clients())
        changed = clients()
        changed[2]["spec"]["containers"][0]["volumeMounts"] = []
        with self.assertRaises(refresh.UnsafeRefresh):
            refresh.check_nfs_mounts(volume, changed)
    def test_forward_refresh_preserves_exact_running_export(self):
        volume, manager, pod = export()
        self.assertEqual(refresh.check_export(volume, manager, pod, TARGET), "nfs://stable/test")
        for mutate in [lambda v, m, p: v["spec"].update(accessMode="rwo"),
                       lambda v, m, p: m["spec"].update(image="longhornio/longhorn-share-manager:v1.12.1"),
                       lambda v, m, p: m["status"].update(state="starting"),
                       lambda v, m, p: m["status"].update(endpoint="nfs://changed/test")]:
            changed = copy.deepcopy((volume, manager, pod))
            mutate(*changed)
            with self.assertRaises(refresh.UnsafeRefresh):
                refresh.check_export(*changed, TARGET)

    def test_refuse_foreign_owner_unready_or_terminating_export(self):
        for mutate in [lambda p: p["metadata"]["ownerReferences"][0].update(uid="foreign"),
                       lambda p: p["metadata"]["ownerReferences"][0].update(controller=False),
                       lambda p: p["metadata"].update(deletionTimestamp="now"),
                       lambda p: p["status"]["conditions"][0].update(status="False")]:
            volume, manager, pod = export()
            mutate(pod)
            with self.assertRaises(refresh.UnsafeRefresh):
                refresh.check_export(volume, manager, pod, TARGET)

    def test_refuse_downgrades_equal_versions_and_unreviewed_images(self):
        for old in [TARGET, "longhornio/longhorn-share-manager:v1.14.0", "longhornio/longhorn-share-manager:v0.11.1", "untrusted/share-manager:v1.11.1", "longhornio/longhorn-share-manager:latest"]:
            volume, manager, pod = export()
            pod["spec"]["containers"][0]["image"] = old
            with self.assertRaises(refresh.UnsafeRefresh):
                refresh.check_export(volume, manager, pod, TARGET)

    def test_bound_original_claims_and_ready_client_identity(self):
        volume, _, _ = export()
        claims, pods = refresh.consumers(volume, clients())
        self.assertEqual(claims, {("app", "data", "claim-uid", "pv-uid")})
        self.assertEqual(pods, {("app", "consumer", "client-uid")})
        for index, field, value in [(0, "phase", "Released"), (1, "phase", "Lost"), (2, "phase", "Pending")]:
            changed = clients()
            changed[index]["status"][field] = value
            with self.assertRaises(refresh.UnsafeRefresh):
                refresh.consumers(volume, changed)
        changed = clients()
        changed[1]["metadata"]["uid"] = "replacement-claim"
        with self.assertRaises(refresh.UnsafeRefresh):
            refresh.consumers(volume, changed)

    def test_unready_and_terminating_clients_block_refresh(self):
        volume, _, _ = export()
        for mutate in [lambda p: p["metadata"].update(deletionTimestamp="now"),
                       lambda p: p["status"]["conditions"][0].update(status="False")]:
            changed = clients()
            mutate(changed[2])
            with self.assertRaises(refresh.UnsafeRefresh):
                refresh.consumers(volume, changed)
        with self.assertRaises(refresh.UnsafeRefresh):
            refresh.consumers(volume, clients()[:2])

    def test_process_drift_and_stale_unrelated_recovery_stop_refresh(self):
        image = "docker.io/longhornio/longhorn-engine:v1.13.0"
        now = dt.datetime.now(dt.timezone.utc)
        items = []
        backups = []
        for name in ["selected", "unrelated"]:
            items.append({"kind": "Volume", "metadata": {"name": name},
                          "spec": {"dataEngine": "v1", "image": image, "numberOfReplicas": 3},
                          "status": {"state": "attached", "robustness": "healthy", "currentImage": image, "lastBackup": name}})
            items.append({"kind": "Engine", "metadata": {"name": name + "-e"},
                          "spec": {"volumeName": name, "active": True, "image": image},
                          "status": {"currentState": "running", "currentImage": image,
                                     "replicaModeMap": {name+str(i): "RW" for i in range(3)}}})
            items.extend({"kind": "Replica", "metadata": {"name": name+str(i)},
                          "spec": {"volumeName": name, "active": True, "image": image, "nodeID": "metal"+str(i)},
                          "status": {"currentState": "running", "currentImage": image}} for i in range(3))
            backups.append({"metadata": {"name": name}, "status": {"volumeName": name, "state": "Completed", "progress": 100,
                           "url": "s3://recovery", "snapshotCreatedAt": now.isoformat()}})
        refresh.recovery_guard(items, backups, refresh.guards(), image, now)
        changed = copy.deepcopy(items)
        changed[-1]["status"]["currentImage"] = "docker.io/longhornio/longhorn-engine:v1.12.1"
        with self.assertRaises(refresh.UnsafeRefresh):
            refresh.recovery_guard(changed, backups, refresh.guards(), image, now)
        changed_backups = copy.deepcopy(backups)
        changed_backups[-1]["status"]["snapshotCreatedAt"] = (now-dt.timedelta(days=2)).isoformat()
        with self.assertRaises(refresh.UnsafeRefresh):
            refresh.recovery_guard(items, changed_backups, refresh.guards(), image, now)

    def test_native_refresh_uses_exact_pod_identity_and_preserves_original_clients(self):
        volume, manager, old = export()
        image = "docker.io/longhornio/longhorn-engine:v1.13.0"
        volume["kind"] = "Volume"
        volume["spec"].update(dataEngine="v1", image=image, numberOfReplicas=3)
        volume["status"].update(state="attached", robustness="healthy", currentImage=image, lastBackup="newest")
        engine = {"kind": "Engine", "metadata": {"name": "engine"}, "spec": {"volumeName": "test", "active": True, "image": image},
                  "status": {"currentState": "running", "currentImage": image, "replicaModeMap": {"r0": "RW", "r1": "RW", "r2": "RW"}}}
        replicas = [{"kind": "Replica", "metadata": {"name": f"r{i}"},
                     "spec": {"volumeName": "test", "active": True, "nodeID": f"metal{i}", "failedAt": "", "image": image},
                     "status": {"currentState": "running", "currentImage": image}} for i in range(3)]
        backups = [{"metadata": {"name": "newest"}, "status": {"state": "Completed", "progress": 100, "volumeName": "test",
                    "url": "s3://recovery", "snapshotCreatedAt": dt.datetime.now(dt.timezone.utc).isoformat()}}]
        replacement = copy.deepcopy(old)
        replacement["metadata"]["uid"] = "pod-replacement"
        replacement["spec"]["containers"][0]["image"] = TARGET
        def get(resource, name=None, namespace="longhorn-system"):
            if resource == "daemonset":
                return {"metadata": {"generation": 1}, "status": {"desiredNumberScheduled": 3, "numberReady": 3,
                        "updatedNumberScheduled": 3, "observedGeneration": 1}}
            if resource == "settings.longhorn.io":
                return {"value": {"default-engine-image": image, "current-longhorn-version": "v1.13.0", "share-manager-image": TARGET}[name]}
            if resource == "volumes.longhorn.io,engines.longhorn.io,replicas.longhorn.io": return {"items": [volume, engine] + replicas}
            if resource == "backups.longhorn.io": return {"items": backups}
            if resource == "sharemanagers.longhorn.io": return manager
            if resource == "pod": return old
            if resource == "pods": return {"items": [replacement]}
            if resource == "pv,pvc,pod": return {"items": clients()}
            self.fail(resource)
        with patch.object(refresh, "get", side_effect=get), patch.object(refresh, "run", return_value="nfs\n") as native, \
                patch.object(refresh.sys, "argv", ["refresh", "test", "--image", TARGET, "--apply"]):
            refresh.main()
        deletions = [call for call in native.call_args_list if call.args[0][2] == "delete"]
        self.assertEqual(len(deletions), 1)
        self.assertEqual(native.call_count, 3)
        command, payload = deletions[0].args
        self.assertEqual(command, ["kubectl", "--request-timeout=20s", "delete", "--raw",
                                  "/api/v1/namespaces/longhorn-system/pods/share-manager-test", "-f", "-"])
        import json
        self.assertEqual(json.loads(payload)["preconditions"], {"uid": "pod-original", "resourceVersion": "12"})


if __name__ == "__main__":
    unittest.main()
