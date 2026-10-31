#!/usr/bin/env python3
"""Create a writable DR scratch copy, push a branch, and merge a native PR.

Only creates repositories in ci named dr-test-*. Never promotes a production
mirror or writes to GitHub. Supply scoped CI and source-read Gitea tokens in
the environment. The generated local workflows are optional inputs.
"""
import argparse
import base64
import datetime as dt
import json
import os
import pathlib
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:13000")
    parser.add_argument("--internal-url", default="http://gitea-http.gitea.svc.cluster.local:3000")
    parser.add_argument("--source", choices=["wowdecomp", "wowdecomp15595"], default="wowdecomp")
    parser.add_argument("--workflows", type=pathlib.Path)
    parser.add_argument("--evidence", type=pathlib.Path, required=True)
    args = parser.parse_args()
    token = os.environ["GITEA_TOKEN"]
    source_token = os.environ["GITEA_SOURCE_TOKEN"]

    def api(method, path, data=None, credential=token):
        request = urllib.request.Request(args.url + "/api/v1" + path,
                                         data=json.dumps(data).encode() if data is not None else None,
                                         headers={"Authorization": "token " + credential,
                                                  "Content-Type": "application/json"}, method=method)
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = response.read()
            return json.loads(payload) if payload else None

    name = "dr-test-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    source = api("GET", "/repos/github-dr/" + args.source, credential=source_token)
    if not source["mirror"]: raise SystemExit("Expected an unchanged read-only production mirror")
    snapshot = source["updated_at"]
    # Migrate to a fresh writable scratch repository through the internal LAN
    # path; the source receives only authenticated reads.
    scratch = api("POST", "/repos/migrate", {
        "clone_addr": args.internal_url + "/github-dr/" + args.source + ".git",
        "auth_username": "mirror-bot", "auth_password": source_token,
        "repo_owner": "ci", "repo_name": name, "mirror": False,
        "private": True, "issues": False, "pull_requests": False,
        "releases": False, "wiki": False})
    if scratch["mirror"]: raise SystemExit("Scratch copy must be writable")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_COUNT="1",
               GIT_CONFIG_KEY_0="http." + args.url + "/.extraHeader",
               GIT_CONFIG_VALUE_0="Authorization: Basic " + base64.b64encode(("ci-bot:" + token).encode()).decode())
    with tempfile.TemporaryDirectory(prefix="gitea-dr-") as directory:
        root = pathlib.Path(directory)
        def git(*command):
            return subprocess.check_output(["git", "-C", str(root), *command], env=env, stderr=subprocess.PIPE).decode().strip()
        subprocess.run(["git", "clone", args.url + "/ci/" + name + ".git", str(root)],
                       env=env, check=True, capture_output=True)
        git("config", "user.name", "Gitea recovery exercise")
        git("config", "user.email", "sam@cooked.beer")
        refs = git("for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes/origin", "refs/tags")
        git("switch", "-c", "recovery/proof")
        (root / "DR-EXERCISE.txt").write_text("Native Gitea development and PR exercise.\n")
        if args.workflows:
            import shutil
            shutil.copytree(args.workflows / ".gitea", root / ".gitea", dirs_exist_ok=True)
        git("add", "DR-EXERCISE.txt")
        if args.workflows: git("add", ".gitea")
        git("commit", "-m", "Prove local writable failover without modifying production mirrors")
        commit = git("rev-parse", "HEAD")
        git("push", "origin", "recovery/proof")
        pr = api("POST", "/repos/ci/" + name + "/pulls", {
            "title": "Verify native disaster recovery development",
            "head": "recovery/proof", "base": scratch["default_branch"],
            "body": "Controlled exercise in a disposable local copy. Production mirrors remain read-only."})
        # Native mergeability calculation is asynchronous.
        for _ in range(20):
            info = api("GET", "/repos/ci/" + name + "/pulls/" + str(pr["number"]))
            if info.get("mergeable"): break
            time.sleep(1)
        api("PUT", "/repos/ci/" + name + "/actions/secrets/SOURCE_TOKEN", {"data": source_token})
        api("PATCH", "/repos/ci/" + name, {"has_actions": True})
        api("POST", "/repos/ci/" + name + "/pulls/" + str(pr["number"]) + "/merge", {"Do": "merge"})
        result = api("GET", "/repos/ci/" + name + "/pulls/" + str(pr["number"]))
        if not result.get("merged"): raise SystemExit("Native PR was not merged")
    after = api("GET", "/repos/github-dr/" + args.source, credential=source_token)
    if not after["mirror"]: raise SystemExit("Production mirror state unexpectedly changed")
    evidence = {"scratch_repository": "ci/" + name, "source_repository": source["full_name"],
                "source_updated_at_before": snapshot, "branch_commit": commit,
                "listed_refs": refs.splitlines(), "native_pr": pr["number"],
                "pr_merged": result["merged"], "production_mirror_preserved": True,
                "linux_actions": "pending execution", "restore": "separate acceptance test"}
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({k: evidence[k] for k in ["scratch_repository", "native_pr", "pr_merged", "production_mirror_preserved"]}))


if __name__ == "__main__": main()
