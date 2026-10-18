#!/usr/bin/env python3
"""Check helper scripts using the digest-pinned Python images in the PR."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]
HELPERS = {
    "infra/kyverno/automation/github-issue-sync-cronjob.yaml": "sync",
    "infra/longhorn/weekly-backup-retention.yaml": "retention",
    "infra/observability/gatus-github-issue-sync-cronjob.yaml": "sync",
    "ops/longhorn/reviewed-backup-retention-job.yaml": "retention",
}
SOURCES = {
    "kyverno": "infra/kyverno/automation/github-issue-sync-configmap.yaml",
    "gatus": "infra/observability/gatus-github-issue-sync-configmap.yaml",
}

# Only the standard library is needed inside the candidate images. Load the
# embedded scripts as modules so their live API entrypoints are never invoked.
RUNTIME_CHECKS = r'''
import json
import sys
import unittest
from datetime import datetime, timezone

sources = json.load(sys.stdin)
helpers = {}
for name, source in sources.items():
    namespace = {"__name__": name}
    exec(compile(source, name + "/sync.py", "exec"), namespace)
    helpers[name] = namespace


class IssueSyncSmokeTests(unittest.TestCase):
    def test_kyverno_result_timestamps(self):
        parse = helpers["kyverno"]["parse_result_time"]
        expected = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(parse({"timestamp": "2026-01-01T00:00:00Z"}, None), expected)
        self.assertEqual(parse({"timestamp": {"seconds": int(expected.timestamp())}}, None), expected)
        self.assertIsNone(parse({"timestamp": "invalid"}, None))

    def test_gatus_requires_recent_consecutive_successes(self):
        helper = helpers["gatus"]
        helper.update(MAX_RESULT_AGE_SECONDS=900, SUCCESS_THRESHOLD=2)
        healthy = {"success": True, "timestamp": datetime.now(timezone.utc).isoformat()}
        check = helper["endpoint_is_stably_healthy"]
        self.assertTrue(check({"results": [healthy, healthy]}))
        self.assertFalse(check({"results": [healthy]}))
        self.assertFalse(check({"results": [healthy, {**healthy, "success": False}]}))
        self.assertFalse(check({"results": [healthy, {**healthy, "timestamp": "2000-01-01T00:00:00Z"}]}))


loader = unittest.defaultTestLoader
suite = loader.loadTestsFromTestCase(IssueSyncSmokeTests)
for pattern in ("test_longhorn_retire_backup_history.py", "test_longhorn_upgrade_engines.py"):
    suite.addTests(loader.discover("scripts/tests", pattern=pattern))
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
'''


def helper_images() -> list[str]:
    images = set()
    for path, name in HELPERS.items():
        documents = list(yaml.safe_load_all((ROOT / path).read_text()))
        jobs = [doc for doc in documents if isinstance(doc, dict) and doc.get("kind") in {"CronJob", "Job"}]
        if len(jobs) != 1:
            raise ValueError(f"Expected one helper Job or CronJob in {path}")
        job = jobs[0]
        spec = job["spec"]["jobTemplate"]["spec"] if job["kind"] == "CronJob" else job["spec"]
        matches = [container["image"] for container in spec["template"]["spec"]["containers"] if container["name"] == name]
        if len(matches) != 1 or not re.fullmatch(r"python:[\w.-]+@sha256:[a-f0-9]{64}", matches[0]):
            raise ValueError(f"Expected one digest-pinned official Python image for {name} in {path}")
        images.add(matches[0])
        print(f"{path}: {matches[0]}", flush=True)
    return sorted(images)


def main() -> None:
    sources = {
        name: yaml.safe_load((ROOT / path).read_text())["data"]["sync.py"]
        for name, path in SOURCES.items()
    }
    for image in helper_images():
        print(f"Testing Python helpers on {image}", flush=True)
        result = subprocess.run(
            [
                "docker", "run", "--rm", "--interactive", "--network", "none",
                "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", f"{os.getuid()}:{os.getgid()}",
                "--mount", f"type=bind,source={ROOT},target=/repo,readonly",
                "--workdir", "/repo", "--entrypoint", "python", image,
                "-B", "-c", RUNTIME_CHECKS,
            ],
            input=json.dumps(sources), text=True, check=False, timeout=180,
        )
        if result.returncode:
            raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
