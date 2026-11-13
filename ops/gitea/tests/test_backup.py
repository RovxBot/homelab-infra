"""Exercise the real backup supervisor without cluster or backup credentials."""

import json
import os
from pathlib import Path
import re
import signal
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
    target = next((a.split("/", 1)[1] for a in args if a.startswith("deployment/")), None)
    if "get" in args and target:
        print(state[target])
    elif "scale" in args:
        replicas = int(next(a.split("=", 1)[1] for a in args if a.startswith("--replicas=")))
        if "--current-replicas=1" in args and state[target] != 1:
            sys.exit(1)
        if replicas == 1 and os.environ.get("TEST_FAIL_RESUME") and not (root / "resume-failed").exists():
            (root / "resume-failed").touch()
            sys.exit(1)
        state[target] = replicas
        state_file.write_text(json.dumps(state))
        record(f"scale {target} {replicas}")
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
    print("[]" if action == "snapshots" else "{}")
elif program == "pg_dump":
    hang("dump")
    Path(next(a.split("=", 1)[1] for a in args if a.startswith("--file="))).write_text("database")
elif program == "df":
    print("Filesystem 1024-blocks Used Available Capacity Mounted on")
    print("test 9999999 1 9999998 1% /")
'''


class BackupTests(unittest.TestCase):
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
        for name in ("kubectl", "restic", "pg_dump", "pg_restore", "pg_dumpall", "df"):
            (self.root / "bin" / name).symlink_to(executable)
        # Redirect only absolute mount paths; preserve deployment/gitea names.
        script = re.sub(r"(?<![\w/.-])/(work|gitea|protection|mirror|recovery|scripts)\b",
                        lambda match: str(self.root / match[1]), SCRIPT.read_text())
        self.script = self.root / "backup.sh"
        self.script.write_text(script)
        self.env = dict(os.environ, PATH=str(self.root / "bin") + ":" + os.environ["PATH"],
                        TEST_ROOT=str(self.root), DATABASE_ADMIN_PASSWORD="test-fixture")

    def run_backup(self, **env):
        return subprocess.run(["bash", str(self.script)], env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=10)

    def assert_recovered(self, failed):
        self.assertEqual(json.loads((self.root / "replicas.json").read_text()), self.expected)
        self.assertEqual((self.root / "protection/backup-failure.epoch").exists(), failed)
        self.assertEqual((self.root / "protection/backup-success.epoch").exists(), not failed)

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


if __name__ == "__main__":
    unittest.main()
