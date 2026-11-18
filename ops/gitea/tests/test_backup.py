"""Exercise the real backup supervisor without cluster or backup credentials."""

import json
import os
from pathlib import Path
import re
import signal
import shutil
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "backup.sh"
FAKE_COMMAND = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import signal
import sys
import time

root = Path(os.environ["TEST_ROOT"])
program = Path(sys.argv[0]).name
args = sys.argv[1:]
state_file = root / "replicas.json"
state = json.loads(state_file.read_text())

def record(event):
    with (root / "events").open("a") as stream:
        stream.write(event + "\n")

def hang(stage):
    record(stage + " started")
    if os.environ.get("TEST_HANG") == stage:
        def terminate(*_):
            record(stage + " terminated")
            sys.exit(143)
        signal.signal(signal.SIGTERM, terminate)
        while True:
            time.sleep(0.05)

if program == "kubectl":
    if os.environ.get("TEST_REAL_API"):
        os.execv(os.environ["REAL_KUBECTL"], [os.environ["REAL_KUBECTL"], *args])
    config = Path(os.environ.get("KUBECONFIG", "/missing"))
    if not config.is_file() or "tokenFile:" not in config.read_text() or "certificate-authority:" not in config.read_text():
        sys.exit(90)  # The original localhost fallback must fail these tests.
    if "auth" in args:
        record("authorization checked")
        sys.exit(1 if os.environ.get("TEST_RBAC_DENIED") else 0)
    if os.environ.get("TEST_API_FAILURE"):
        sys.exit(1)
    if "jobs" in args:
        print("other-maintenance|1" if os.environ.get("TEST_ACTIVE_JOB") else "backup-fixture|1")
        sys.exit(0)
    target = next((a.split("/", 1)[1] for a in args if a.startswith("deployment/")), None)
    if "get" in args and target:
        print(state[target])
    elif "scale" in args:
        replicas = int(next(a.split("=", 1)[1] for a in args if a.startswith("--replicas=")))
        if "--current-replicas=1" in args and state[target] != 1:
            sys.exit(1)
        if replicas == 0 and target == os.environ.get("TEST_PARTIAL_SCALE_FAILURE"):
            state[target] = replicas  # Server committed, response was lost.
            state_file.write_text(json.dumps(state))
            record(f"scale {target} 0 response lost")
            sys.exit(1)
        if replicas == 1 and os.environ.get("TEST_RESUME_ALWAYS_FAIL"):
            record(f"restore {target} failed")
            sys.exit(1)
        if replicas == 1 and os.environ.get("TEST_FAIL_RESUME") and not (root / "resume-failed").exists():
            (root / "resume-failed").touch()
            sys.exit(1)
        state[target] = replicas
        state_file.write_text(json.dumps(state))
        record(f"scale {target} {replicas}")
        if replicas == 0: hang("scale-" + target)
elif program == "restic":
    action = args[0]
    hang(action)
    if os.environ.get("TEST_FAIL") == action:
        sys.exit(42)
    if action in ("check", "forget"):
        # All remote work shares the cold timeout; the backup Pod must exit
        # before an application on another node can acquire its RWO volumes.
        if state != {"gitea": 0, "gitea-protection": 0, "gitea-mirror": 0}:
            sys.exit(43)
    if action == "list":
        sys.stdout.write("locked-repository" if os.environ.get("TEST_RESTIC_LOCKED") else "")
    else:
        print("[]" if action == "snapshots" else "{}")
elif program == "pg_dump":
    hang("dump")
    Path(next(a.split("=", 1)[1] for a in args if a.startswith("--file="))).write_text("database")
elif program == "df":
    print("Filesystem 1024-blocks Used Available Capacity Mounted on")
    available = 1 if os.environ.get("TEST_SPACE_LOW") else 9999998
    print(f"test 9999999 1 {available} 1% /")
elif program == "psql":
    print(1048576)

'''


class BackupFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for directory in ("bin", "work", "gitea", "protection", "mirror", "recovery", "scripts"):
            (self.root / directory).mkdir()
        self.expected = {"gitea": 1, "gitea-protection": 1, "gitea-mirror": 0}
        (self.root / "replicas.json").write_text(json.dumps(self.expected))
        (self.root / "events").touch()
        (self.root / "scripts/protected-repositories.json").write_text("{}")
        executable = self.root / "bin/fake-command"
        executable.write_text(FAKE_COMMAND)
        executable.chmod(0o755)
        for name in ("kubectl", "restic", "pg_dump", "pg_restore", "pg_dumpall", "df", "psql"):
            (self.root / "bin" / name).symlink_to(executable)
        # Redirect only absolute mount paths; preserve deployment/gitea names.
        script = re.sub(r"(?<![\w/.-])/(work|gitea|protection|mirror|recovery|scripts)\b",
                        lambda match: str(self.root / match[1]), SCRIPT.read_text())
        self.script = self.root / "backup.sh"
        self.script.write_text(script)
        self.sa = self.root / "serviceaccount"
        self.sa.mkdir()
        (self.sa / "token").write_text("test-serviceaccount-token")
        (self.sa / "ca.crt").write_text("mock-ca-for-command-tests")
        self.env = dict(os.environ, SERVICEACCOUNT_DIR=str(self.sa), BACKUP_JOB_NAME="backup-fixture", PATH=str(self.root / "bin") + ":" + os.environ["PATH"],
                        TEST_ROOT=str(self.root), DATABASE_ADMIN_PASSWORD="test-fixture")

    def run_backup(self, **env):
        return subprocess.run(["bash", str(self.script)], env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=10)

    def assert_recovered(self, failed):
        self.assertEqual(json.loads((self.root / "replicas.json").read_text()), self.expected)
        self.assertEqual((self.root / "protection/backup-failure.epoch").exists(), failed)
        self.assertEqual((self.root / "protection/backup-success.epoch").exists(), not failed)



class BackupTests(BackupFixture, unittest.TestCase):
    def test_success_finishes_remote_work_before_resuming_and_keeps_mirror_retired(self):
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_recovered(failed=False)
        events = (self.root / "events").read_text()
        self.assertNotIn("scale gitea-mirror", events)
        self.assertLess(events.index("check started"), events.index("scale gitea 1"))
        self.assertLess(events.index("forget started"), events.index("scale gitea-protection 1"))

    def test_preflight_failure_never_stops_services(self):
        result = self.run_backup(TEST_FAIL="snapshots")
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertNotIn("scale ", (self.root / "events").read_text())
        self.assert_recovered(failed=True)

    def test_capture_failure_restores_services_without_running_retention(self):
        result = self.run_backup(TEST_FAIL="backup")
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assert_recovered(failed=True)
        self.assertNotIn("forget started", (self.root / "events").read_text())

    def test_capture_timeout_terminates_writer_and_restores_services(self):
        result = self.run_backup(TEST_HANG="backup", COLD_BACKUP_TIMEOUT="0.8s")
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assert_recovered(failed=True)
        events = (self.root / "events").read_text()
        self.assertLess(events.index("backup terminated"), events.index("scale gitea 1"))

    def test_sigterm_during_blocked_database_dump_restores_promptly(self):
        process = subprocess.Popen(["bash", str(self.script)], env=dict(self.env, TEST_HANG="dump"),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while "dump started" not in (self.root / "events").read_text():
                self.assertLess(time.monotonic(), deadline, "capture did not start")
                time.sleep(0.02)
            process.send_signal(signal.SIGTERM)
            _, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 143, stderr)
            self.assert_recovered(failed=True)
            events = (self.root / "events").read_text()
            self.assertLess(events.index("dump terminated"), events.index("scale gitea 1"))
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    def test_sigterm_during_partial_scale_down_restores_before_exit(self):
        process = subprocess.Popen(["bash", str(self.script)],
                                   env=dict(self.env, TEST_HANG="scale-gitea-protection"),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while "scale-gitea-protection started" not in (self.root / "events").read_text():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
            process.send_signal(signal.SIGTERM)
            _, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 143, stderr)
            self.assert_recovered(failed=True)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    def test_integrity_check_shares_cold_timeout_and_does_not_hold_volumes_indefinitely(self):
        result = self.run_backup(TEST_HANG="check", COLD_BACKUP_TIMEOUT="0.8s")
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assert_recovered(failed=True)
        events = (self.root / "events").read_text()
        self.assertLess(events.index("check terminated"), events.index("scale gitea 1"))
        self.assertNotIn("forget started", events)

    def test_integrity_failure_leaves_services_up_and_does_not_forget(self):
        result = self.run_backup(TEST_FAIL="check")
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assert_recovered(failed=True)
        self.assertNotIn("forget started", (self.root / "events").read_text())

    def test_failed_resume_is_retried_by_exit_trap(self):
        result = self.run_backup(TEST_FAIL_RESUME="1")
        self.assertNotEqual(result.returncode, 0)
        self.assert_recovered(failed=True)

    def test_missing_token_and_ca_fail_without_downtime(self):
        for filename in ("token", "ca.crt"):
            with self.subTest(filename=filename):
                path = self.sa / filename
                value = path.read_text()
                path.unlink()
                result = self.run_backup()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("scale ", (self.root / "events").read_text())
                self.assert_recovered(failed=True)
                path.write_text(value)

    def test_preflight_rejects_rbac_api_capacity_and_overlapping_jobs(self):
        for setting in ("TEST_RBAC_DENIED", "TEST_API_FAILURE", "TEST_SPACE_LOW", "TEST_ACTIVE_JOB", "TEST_RESTIC_LOCKED"):
            with self.subTest(setting=setting):
                result = self.run_backup(**{setting: "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("scale ", (self.root / "events").read_text())
                self.assert_recovered(failed=True)

    def test_all_replica_counts_checked_before_first_scale(self):
        (self.root / "replicas.json").write_text(json.dumps({**self.expected, "gitea": 0}))
        result = self.run_backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("scale ", (self.root / "events").read_text())

    def test_partial_scale_down_including_lost_response_recovers(self):
        result = self.run_backup(TEST_PARTIAL_SCALE_FAILURE="gitea")
        self.assertNotEqual(result.returncode, 0)
        self.assert_recovered(failed=True)
        self.assertNotIn("dump started", (self.root / "events").read_text())

    def test_persistent_restore_failure_is_bounded_and_never_reports_success(self):
        result = self.run_backup(TEST_RESUME_ALWAYS_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / "protection/backup-failure.epoch").exists())
        self.assertFalse((self.root / "protection/backup-success.epoch").exists())
        self.assertIn("restoration incomplete", result.stderr)
        self.assertFalse((self.root / "protection/backup.lock").exists())

    def test_failure_marker_only_cleared_by_verified_success(self):
        marker = self.root / "protection/backup-failure.epoch"
        marker.write_text("1")
        self.assertNotEqual(self.run_backup(TEST_FAIL="check").returncode, 0)
        self.assertTrue(marker.exists())
        self.assertEqual(self.run_backup().returncode, 0)
        self.assertFalse(marker.exists())

    def test_shared_locks_prevent_overlap_without_removing_others_lock(self):
        for name in ("backup.lock", "maintenance.lock"):
            with self.subTest(name=name):
                lock = self.root / "protection" / name
                lock.mkdir()
                self.assertNotEqual(self.run_backup().returncode, 0)
                self.assertTrue(lock.exists())
                self.assertNotIn("scale ", (self.root / "events").read_text())
                lock.rmdir()


class KubernetesTLSAuthenticationTests(BackupFixture, unittest.TestCase):
    """A real kubectl, with no workstation kubeconfig, against a TLS API fixture."""

    def setUp(self):
        super().setUp()
        self.real_kubectl = shutil.which("kubectl")
        if not self.real_kubectl or not shutil.which("openssl"):
            self.skipTest("kubectl and openssl are required for TLS integration tests")

    def api_server(self, mode="ok"):
        cert, key = self.sa / "ca.crt", self.sa / "server.key"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", "1",
                        "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                       check=True, capture_output=True)
        self.requests = []
        requests = self.requests

        class API(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                requests.append((self.command, self.path, self.headers.get("Authorization")))
                path = self.path.split("?", 1)[0]
                code = 200
                if mode == "unauthorized" or self.headers.get("Authorization") != "Bearer test-serviceaccount-token":
                    code, data = 401, {"kind": "Status", "apiVersion": "v1", "reason": "Unauthorized", "code": 401}
                elif mode == "forbidden" and "/deployments/" in path:
                    code, data = 403, {"kind": "Status", "apiVersion": "v1", "reason": "Forbidden", "code": 403}
                elif path == "/api":
                    data = {"kind": "APIVersions", "apiVersion": "v1", "versions": ["v1"]}
                elif path == "/apis":
                    data = {"kind": "APIGroupList", "apiVersion": "v1", "groups": [
                        {"name": group, "versions": [{"groupVersion": group + "/v1", "version": "v1"}],
                         "preferredVersion": {"groupVersion": group + "/v1", "version": "v1"}}
                        for group in ("apps", "batch", "authorization.k8s.io")]}
                elif path in ("/api/v1", "/apis/apps/v1", "/apis/batch/v1", "/apis/authorization.k8s.io/v1"):
                    gv = path.removeprefix("/apis/") if path.startswith("/apis/") else "v1"
                    data = {"kind": "APIResourceList", "apiVersion": "v1", "groupVersion": gv,
                            "resources": [{"name": name, "singularName": kind.lower(), "namespaced": ns,
                                           "kind": kind, "verbs": ["get", "list", "create", "patch", "update"]}
                                          for name, kind, ns in (("deployments", "Deployment", True),
                                                                ("jobs", "Job", True),
                                                                ("pods", "Pod", True),
                                                                ("selfsubjectaccessreviews", "SelfSubjectAccessReview", False))]}
                elif "/deployments/" in path:
                    name = path.rsplit("/", 1)[-1]
                    data = {"kind": "Deployment", "apiVersion": "apps/v1", "metadata": {"name": name},
                            "spec": {"replicas": 0 if name == "gitea-mirror" else 1}}
                elif path.endswith("/jobs"):
                    data = {"kind": "JobList", "apiVersion": "batch/v1", "items": []}
                else:
                    code, data = 404, {"kind": "Status", "apiVersion": "v1", "code": 404}
                self.respond(code, data)

            def do_POST(self):
                requests.append((self.command, self.path, self.headers.get("Authorization")))
                self.rfile.read(int(self.headers["Content-Length"]))
                data = {"apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectAccessReview"}
                data["status"] = {"allowed": mode != "rbac-denied"}
                self.respond(201, data)

            def respond(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), API)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.env.update(TEST_REAL_API="1", REAL_KUBECTL=self.real_kubectl,
                        KUBERNETES_API_SERVER=f"https://127.0.0.1:{server.server_port}",
                        HTTPS_PROXY="", HTTP_PROXY="", ALL_PROXY="", NO_PROXY="127.0.0.1")
        return cert

    def test_real_client_authenticates_with_projected_token_and_ca_before_backup(self):
        self.api_server()
        result = self.run_backup(TEST_FAIL="snapshots")  # Stop before mutation.
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertTrue(any("/deployments/gitea" in path for _, path, _ in self.requests))
        self.assertTrue(any(method == "POST" for method, _, _ in self.requests))
        self.assertTrue(all(auth == "Bearer test-serviceaccount-token" for _, _, auth in self.requests))
        self.assertNotIn("test-serviceaccount-token", result.stdout + result.stderr)
        self.assertNotIn("scale ", (self.root / "events").read_text())

    def test_real_client_rejects_invalid_ca(self):
        cert = self.api_server()
        cert.write_text("invalid-ca")
        result = self.run_backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.requests)
        self.assert_recovered(failed=True)

    def test_real_client_rejects_untrusted_ca(self):
        self.api_server()
        other = self.sa / "other.crt"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(self.sa / "other.key"), "-out", str(other), "-days", "1",
                        "-subj", "/CN=other"], check=True, capture_output=True)
        (self.sa / "ca.crt").write_bytes(other.read_bytes())
        result = self.run_backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.requests)
        self.assert_recovered(failed=True)

    def test_real_client_rejects_unreachable_api(self):
        self.api_server()
        self.env.update(KUBERNETES_API_SERVER="https://127.0.0.1:1")
        self.assertNotEqual(self.run_backup().returncode, 0)
        self.assert_recovered(failed=True)

    def test_real_client_authentication_and_authorization_fail_without_downtime(self):
        for mode in ("unauthorized", "forbidden", "rbac-denied"):
            with self.subTest(mode=mode):
                self.api_server(mode)
                result = self.run_backup()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("scale ", (self.root / "events").read_text())
                self.assert_recovered(failed=True)


if __name__ == "__main__":
    unittest.main()
