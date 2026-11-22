#!/usr/bin/env python3
"""Create/update one reviewed database PR after grype_db.py --prepare.

GH_TOKEN must be a repository-scoped token able to write contents and PRs and
trigger normal pull_request CI (the existing WORKFLOW_PR_TOKEN convention).
"""

import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "ci/grype-database-refresh"
PATHS = ["infra/kubescape/helmrelease.yaml", "infra/kubescape/grype-database.json", "infra/kubescape/rendered-inventory.json"]


def run(*args, **kwargs):
    return subprocess.check_output(args, cwd=ROOT, text=True, **kwargs).strip()


def body(lock):
    previous = lock["previous"]
    return ("Refresh the immutable Grype database for the curated daily Kubescape assessments.\n\n"
            f"Previous index: `{previous['indexDigest']}`; build: `{previous['metadata'].get('built', 'unknown')}`; "
            f"archive: `{previous['metadata'].get('checksum', 'unknown')}`.\n\n"
            f"New index: `{lock['indexDigest']}`; platform manifest: `{lock['manifestDigest']}`.\n\n"
            f"New build: `{lock['metadata']['built']}`; schema: `{lock['metadata']['schemaVersion']}`; "
            f"archive: `{lock['metadata']['checksum']}`; SQLite: `{lock['database']['sha256']}`.\n\n"
            f"Independent publisher: {lock['publisher']['url']} ({lock['publisher']['method']}); "
            "all archive identity fields match. Registry index, platform manifest, config, all platform layers, "
            "archive SHA-256 and SQLite quick_check verified. No independent signature claimed.\n\n"
            f"Replacement needs at least {lock['storage']['requiredFreeBytes']} free bytes with the old DB retained. "
            "Live headroom and Longhorn health must be rechecked before merge.\n\n"
            "The engine rollout annotation tracks the mirror index; Recreate preserves the RWO PVC. "
            "The two custom daily assessment schedules remain enabled; chart-wide schedules and automatic event routing remain disabled.\n\n"
            "Requires normal CI and human-reviewed merge, normally on Monday. The verified database is fresh for 7 days and usable in grace through day 14; integrity gates still apply. After Flux reconciliation the recurring runner performs its "
            "read-only cache audit and bounded image assessments; readiness alone is insufficient.\n\n"
            "Tracking #2135 (leave open).\n")


def main():
    lock = json.loads((ROOT / PATHS[1]).read_text())
    changes = run("git", "diff", "--name-only").splitlines()
    # Include the first, untracked lock file, but never unrelated working files.
    untracked = run("git", "ls-files", "--others", "--exclude-standard").splitlines()
    if not changes and not untracked:
        print("No database refresh needed")
        return
    if set(changes + untracked) - set(PATHS):
        raise RuntimeError("Refusing to publish unrelated changes")
    existing = json.loads(run("gh", "pr", "list", "--base", "main", "--head", BRANCH,
                              "--state", "open", "--json", "number"))
    if len(existing) > 1:
        raise RuntimeError("Multiple updater PRs require operator review")
    remote = run("git", "ls-remote", "origin", "refs/heads/" + BRANCH)
    lease = ""
    if remote:
        lease = remote.split()[0]
        run("git", "fetch", "origin", BRANCH)
        prior_paths = run("git", "diff", "--name-only", "origin/main...FETCH_HEAD").splitlines()
        if set(prior_paths) - set(PATHS):
            raise RuntimeError("Updater branch has unrelated changes; refusing overwrite")
        prior = json.loads(run("git", "show", "FETCH_HEAD:" + PATHS[1]))
        from grype_db import timestamp
        if (prior["metadata"]["built"] == lock["metadata"]["built"]
                and prior["metadata"]["checksum"] != lock["metadata"]["checksum"]):
            raise RuntimeError("Publisher identity changed for the same build; operator review required")
        if timestamp(prior["metadata"]["built"]) >= timestamp(lock["metadata"]["built"]):
            print("Existing proposal already contains this release or a newer database")
            return
    run("git", "checkout", "-B", BRANCH)
    run("git", "config", "user.name", "github-actions[bot]")
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    run("git", "add", "--", *PATHS)
    title = "chore(kubescape): refresh verified Grype database " + lock["metadata"]["built"]
    run("git", "commit", "-m", title)
    run("gh", "auth", "setup-git")
    run("git", "push", f"--force-with-lease=refs/heads/{BRANCH}:{lease}", "origin", "HEAD:refs/heads/" + BRANCH)
    with tempfile.TemporaryDirectory() as directory:
        text = Path(directory) / "body.md"
        text.write_text(body(lock))
        if existing:
            print(run("gh", "pr", "edit", str(existing[0]["number"]), "--title", title, "--body-file", str(text)))
        else:
            print(run("gh", "pr", "create", "--base", "main", "--head", BRANCH, "--title", title, "--body-file", str(text)))


if __name__ == "__main__":
    main()
