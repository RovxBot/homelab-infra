#!/usr/bin/env python3
"""Publish only verified, non-destructive Git refs to read-only pull mirrors.

GitHub is fetched into a separate candidate ref namespace. Gitea pulls from this
service, never GitHub directly. A network error, removed ref, changed tag,
non-fast-forward branch, failed LFS fetch or failed bundle verification prevents
publication. An API proxy prevents the metadata mirror bypassing that source.
"""
import argparse
import base64
import hashlib
import hmac
import http.server
import json
import os
import pathlib
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


def git(repo, *args, data=None, env=None):
    result = subprocess.run(["git", "--git-dir=" + str(repo), *args], input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, timeout=1800)
    if result.returncode:
        # Do not relay remote error text: it may contain credential-bearing URLs.
        raise RuntimeError("Git operation failed: " + args[0])
    return result.stdout.decode().strip()


def refs(repo, prefix):
    rows = git(repo, "for-each-ref", "--format=%(refname) %(objectname)", prefix)
    return dict(row.split(" ", 1) for row in rows.splitlines())


def verify_changes(repo, old, new):
    for ref, previous in old.items():
        if ref not in new:
            raise RuntimeError("Approval required: deleted " + ref)
        if new[ref] == previous:
            continue
        if ref.startswith("refs/tags/"):
            raise RuntimeError("Approval required: changed " + ref)
        result = subprocess.run(["git", "--git-dir=" + str(repo), "merge-base",
                                 "--is-ancestor", previous, new[ref]],
                                capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError("Approval required: rewritten or unverifiable " + ref)


def sync_repo(root, config, source_url=None, fetch_lfs=True):
    repo = root / "git" / (config["name"] + ".git")
    repo.parent.mkdir(parents=True, exist_ok=True)
    if not repo.exists():
        subprocess.run(["git", "init", "--bare", str(repo)], check=True,
                       capture_output=True)
        git(repo, "config", "http.receivepack", "false")
        git(repo, "config", "uploadpack.hideRefs", "refs/candidate/")
    url = source_url or "https://github.com/" + config["source"] + ".git"
    git(repo, "config", "remote.origin.url", url)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    token = os.environ.get("GITHUB_TOKEN", "")
    if token and source_url is None:
        auth = base64.b64encode(("x-access-token:" + token).encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraHeader",
                   GIT_CONFIG_VALUE_0="Authorization: Basic " + auth)
    git(repo, "fetch", "--atomic", "--prune", "--no-tags", "origin",
        "+refs/heads/*:refs/candidate/heads/*",
        "+refs/tags/*:refs/candidate/tags/*", env=env)
    new = {k.replace("refs/candidate/", "refs/", 1): v
           for k, v in refs(repo, "refs/candidate/").items()}
    branch = "refs/heads/" + config["default_branch"]
    if branch not in new:
        raise RuntimeError("Required default branch is unavailable")
    if config.get("required_commit"):
        git(repo, "cat-file", "-e", config["required_commit"] + "^{commit}")
    old = refs(repo, "refs/heads/") | refs(repo, "refs/tags/")
    verify_changes(repo, old, new)
    # Fetch every reachable LFS object before publishing any new Git refs.
    if fetch_lfs:
        git(repo, "lfs", "fetch", "--all", "origin", env=env)
        git(repo, "lfs", "fsck", "--objects", new[branch], env=env)
    if old:
        directory = root / "bundles" / config["name"]
        directory.mkdir(parents=True, exist_ok=True)
        stamp = str(time.time_ns())
        temporary = directory / (stamp + ".partial")
        git(repo, "bundle", "create", str(temporary), "--branches", "--tags")
        git(repo, "bundle", "verify", str(temporary))
        final = directory / (stamp + ".bundle")
        temporary.replace(final)
        with final.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        final.with_suffix(".sha256").write_text(digest + "  " + final.name + "\n")
    commands = ["start"]
    for ref, sha in sorted(new.items()):
        commands.append("update " + ref + " " + sha + " " + old.get(ref, "0" * 40))
    commands.extend(["prepare", "commit", ""])
    git(repo, "update-ref", "--stdin", data="\n".join(commands).encode())
    git(repo, "symbolic-ref", "HEAD", branch)
    git(repo, "fsck", "--full", "--no-dangling")
    return {"last_success": int(time.time()), "refs": new, "error": None}


class Protection:
    def __init__(self, root, config):
        self.root, self.config = root, config
        self.lock = threading.Lock()
        self.status_path = root / "status.json"
        self.status = json.loads(self.status_path.read_text()) if self.status_path.exists() else {}

    def sync(self):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Synchronization already running")
        try:
            for repo in self.config["repositories"]:
                name = repo["name"]
                previous = self.status.get(name, {})
                try:
                    self.status[name] = sync_repo(self.root, repo)
                    print(json.dumps({"repository": name, "status": "published"}), flush=True)
                except Exception as error:
                    self.status[name] = previous | {"error": str(error), "last_attempt": int(time.time())}
                    print(json.dumps({"repository": name, "status": "blocked", "error": str(error)}), flush=True)
                temporary = self.status_path.with_suffix(".partial")
                temporary.write_text(json.dumps(self.status, indent=2))
                temporary.replace(self.status_path)
        finally:
            self.lock.release()


def handler(state):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, data, content_type="application/json"):
            data = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authenticated(self):
            expected = "Basic " + base64.b64encode(("dr:" + os.environ["GUARD_TOKEN"]).encode()).decode()
            return hmac.compare_digest(self.headers.get("Authorization", ""), expected)

        def dispatch(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                healthy = all(not state.status.get(r["name"], {}).get("error") and
                              time.time() - state.status.get(r["name"], {}).get("last_success", 0) < 4500
                              for r in state.config["repositories"])
                return self.reply(200 if healthy else 503, state.status)
            if path.startswith("/git/"):
                if not self.authenticated():
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="protected-git"')
                    self.end_headers()
                    return
                match = re.fullmatch(r"/git/([a-zA-Z0-9_-]+)\.git(/.*)?", path)
                if not match or match[1] not in {r["name"] for r in state.config["repositories"]}:
                    return self.reply(404, {"error": "unprotected repository"})
                if "git-receive-pack" in self.path:
                    return self.reply(403, {"error": "read-only"})
                if "/info/lfs/" in path:
                    return self.lfs(match[1], path)
                length = int(self.headers.get("Content-Length", "0"))
                if length > 16 * 1024 * 1024:
                    return self.reply(413, {})
                data = self.rfile.read(length)
                env = dict(os.environ, GIT_PROJECT_ROOT=str(state.root),
                           GIT_HTTP_EXPORT_ALL="1", REQUEST_METHOD=self.command,
                           PATH_INFO=path, QUERY_STRING=parsed.query,
                           CONTENT_TYPE=self.headers.get("Content-Type", ""),
                           CONTENT_LENGTH=str(length), REMOTE_USER="dr")
                backend = subprocess.check_output(["git", "--exec-path"]).decode().strip() + "/git-http-backend"
                result = subprocess.run([backend], input=data, capture_output=True, env=env, timeout=300)
                head, sep, body = result.stdout.partition(b"\r\n\r\n")
                if not sep:
                    return self.reply(502, {"error": "Git backend failed"})
                headers = [line.decode().split(": ", 1) for line in head.split(b"\r\n") if b": " in line]
                code = next((int(v.split()[0]) for k, v in headers if k.lower() == "status"), 200)
                self.send_response(code)
                for k, v in headers:
                    if k.lower() != "status": self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if path == "/sync" and self.command == "POST":
                if not self.authenticated(): return self.reply(401, {})
                threading.Thread(target=state.sync, daemon=True).start()
                return self.reply(202, {"status": "requested"})
            if path.startswith("/api/v1/"):
                return self.proxy(path)
            return self.reply(404, {})

        def lfs(self, name, path):
            base = state.root / "git" / (name + ".git") / "lfs" / "objects"
            if path.endswith("/objects/batch") and self.command == "POST":
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                if data.get("operation") != "download": return self.reply(403, {})
                objects = []
                for item in data.get("objects", []):
                    oid = item["oid"]
                    if not re.fullmatch("[0-9a-f]{64}", oid): return self.reply(400, {})
                    file = base / oid[:2] / oid[2:4] / oid
                    if not file.exists(): return self.reply(503, {"error": "missing LFS object"})
                    objects.append({"oid": oid, "size": file.stat().st_size,
                                    "actions": {"download": {
                                        "href": os.environ["GUARD_URL"] + "/git/" + name + ".git/info/lfs/objects/" + oid,
                                        "header": {"Authorization": self.headers["Authorization"]}}}})
                return self.reply(200, {"transfer": "basic", "objects": objects}, "application/vnd.git-lfs+json")
            oid = path.rsplit("/", 1)[-1]
            if self.command == "GET" and re.fullmatch("[0-9a-f]{64}", oid):
                file = base / oid[:2] / oid[2:4] / oid
                if file.exists(): return self.reply(200, file.read_bytes(), "application/octet-stream")
            return self.reply(404, {})

        def proxy(self, path):
            allowed = {r["name"]: r for r in state.config["repositories"]}
            if self.command == "DELETE":
                return self.reply(403, {"error": "Disaster recovery deletion is disabled"})
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if path == "/api/v1/repos/migrate" and self.command == "POST":
                payload = json.loads(data)
                repo = allowed.get(payload.get("repo_name"))
                if not repo or payload.get("repo_owner") != state.config["owner"]:
                    return self.reply(403, {"error": "Repository is outside the explicit allowlist"})
                if repo["name"] not in state.status or state.status[repo["name"]].get("error"):
                    return self.reply(503, {"error": "A verified source snapshot is required"})
                payload.update(clone_addr=os.environ["GUARD_URL"] + "/git/" + repo["name"] + ".git",
                               auth_username="dr", auth_password=os.environ["GUARD_TOKEN"],
                               mirror=True, mirror_interval="0h", private=True,
                               wiki=False, releases=False, issues=False, pull_requests=False)
                # Metadata is synchronized separately by gitea-mirror's GitHub API path.
                payload.pop("auth_token", None)
                data = json.dumps(payload).encode()
            elif self.command not in {"GET", "HEAD"}:
                match = re.match(r"/api/v1/repos/([^/]+)/([^/]+)(/.*)?$", path)
                if not match or match[1] != state.config["owner"] or match[2] not in allowed:
                    return self.reply(403, {"error": "Write outside protected repositories"})
                if self.command == "PATCH" and not match[3]:
                    payload = json.loads(data)
                    if any(k in payload for k in ["name", "mirror", "original_url", "website"]):
                        return self.reply(403, {"error": "Source identity changes need operator review"})
                    payload["mirror_interval"] = "0h"
                    data = json.dumps(payload).encode()
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() in {"authorization", "content-type", "accept"}}
            request = urllib.request.Request(os.environ["GITEA_URL"] + self.path,
                                             data=data if self.command not in {"GET", "HEAD"} else None,
                                             headers=headers, method=self.command)
            try:
                with urllib.request.urlopen(request, timeout=1800) as result:
                    return self.reply(result.status, result.read(), result.headers.get("Content-Type", "application/json"))
            except urllib.error.HTTPError as error:
                return self.reply(error.code, error.read())

        def do_GET(self): self.dispatch()
        def do_HEAD(self): self.dispatch()
        def do_POST(self): self.dispatch()
        def do_PUT(self): self.dispatch()
        def do_PATCH(self): self.dispatch()
        def do_DELETE(self): self.dispatch()

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path("/data"))
    parser.add_argument("--config", type=pathlib.Path, default=pathlib.Path("/scripts/protected-repositories.json"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    state = Protection(args.root, json.loads(args.config.read_text()))
    if args.once:
        state.sync()
        raise SystemExit(1 if any(x.get("error") for x in state.status.values()) else 0)
    def scheduled():
        while True:
            state.sync()
            time.sleep(state.config["interval_seconds"])
    threading.Thread(target=scheduled, daemon=True).start()
    http.server.ThreadingHTTPServer(("0.0.0.0", 8080), handler(state)).serve_forever()


if __name__ == "__main__": main()
