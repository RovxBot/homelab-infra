import importlib.util
import pathlib
import base64
import http.server
import os
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "protect.py"
spec = importlib.util.spec_from_file_location("protect", MODULE_PATH)
protect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protect)


class ProtectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.run_git("init", "-b", "main")
        self.run_git("config", "user.name", "Recovery test")
        self.run_git("config", "user.email", "test@example.invalid")
        self.commit("first")
        self.config = {"name": "example", "source": "owner/example", "default_branch": "main"}
        self.destination = self.root / "state"

    def run_git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.source), *args], stderr=subprocess.DEVNULL).decode().strip()

    def commit(self, message):
        (self.source / "file").write_text(message)
        self.run_git("add", "file")
        self.run_git("commit", "-m", message)
        return self.run_git("rev-parse", "HEAD")

    def sync(self):
        return protect.sync_repo(self.destination, self.config, str(self.source), fetch_lfs=False)

    def published(self):
        return protect.refs(self.destination / "git/example.git", "refs/heads/")

    def test_fast_forward_and_verified_independent_bundle(self):
        before = self.sync()["refs"]
        self.commit("second")
        self.sync()
        bundle = next((self.destination / "bundles/example").glob("*.bundle"))
        restored = self.root / "restored"
        subprocess.run(["git", "clone", "--branch", "main", str(bundle), str(restored)], check=True, capture_output=True)
        self.assertEqual(subprocess.check_output(["git", "-C", str(restored), "rev-parse", "HEAD"]).decode().strip(), before["refs/heads/main"])

    def test_force_push_blocks_and_retains_previous_tip(self):
        before = self.sync()["refs"]
        self.run_git("checkout", "--orphan", "replacement")
        self.commit("rewritten")
        self.run_git("branch", "-f", "main", "HEAD")
        with self.assertRaisesRegex(RuntimeError, "rewritten"):
            self.sync()
        self.assertEqual(self.published(), before)

    def test_deleted_branch_blocks(self):
        self.run_git("branch", "keep")
        before = self.sync()["refs"]
        self.run_git("branch", "-D", "keep")
        with self.assertRaisesRegex(RuntimeError, "deleted"):
            self.sync()
        self.assertEqual(self.published(), before)

    def test_retained_deleted_branch_allows_fast_forward_and_recovery(self):
        self.config["retain_deleted_branches"] = True
        self.run_git("checkout", "-b", "retired")
        retired_tip = self.commit("unmerged history")
        self.run_git("checkout", "main")
        before = self.sync()["refs"]
        self.run_git("branch", "-D", "retired")
        tip = self.commit("new main")
        result = self.sync()
        self.assertEqual(result["refs"]["refs/heads/main"], tip)
        self.assertEqual(result["refs"]["refs/heads/retired"], retired_tip)
        self.assertEqual(result["retained_deleted_branches"], {"refs/heads/retired": retired_tip})
        bundle = next((self.destination / "bundles/example").glob("*.bundle"))
        restored = self.root / "retained.git"
        subprocess.run(["git", "clone", "--mirror", str(bundle), str(restored)], check=True, capture_output=True)
        self.assertEqual(protect.refs(restored, "refs/heads/"), before)
        self.assertEqual(self.sync()["refs"], result["refs"])

    def test_retention_still_blocks_missing_default_branch(self):
        self.config["retain_deleted_branches"] = True
        before = self.sync()["refs"]
        self.run_git("checkout", "-b", "replacement")
        self.run_git("branch", "-D", "main")
        with self.assertRaisesRegex(RuntimeError, "default branch"):
            self.sync()
        self.assertEqual(self.published(), before)

    def test_retention_still_blocks_deleted_tag(self):
        self.config["retain_deleted_branches"] = True
        self.run_git("tag", "v1")
        before = self.sync()["refs"]
        self.run_git("tag", "-d", "v1")
        with self.assertRaisesRegex(RuntimeError, "deleted refs/tags/v1"):
            self.sync()
        self.assertEqual(protect.refs(self.destination / "git/example.git", "refs/tags/"),
                         {"refs/tags/v1": before["refs/tags/v1"]})

    def test_retained_branch_recreated_with_rewritten_history_blocks(self):
        self.config["retain_deleted_branches"] = True
        self.run_git("branch", "retired")
        before = self.sync()["refs"]
        self.run_git("branch", "-D", "retired")
        self.sync()
        self.run_git("checkout", "--orphan", "retired")
        self.commit("replacement history")
        with self.assertRaisesRegex(RuntimeError, "rewritten"):
            self.sync()
        self.assertEqual(self.published(), before)

    def test_changed_tag_blocks(self):
        self.run_git("tag", "v1")
        self.sync()
        self.commit("second")
        self.run_git("tag", "-f", "v1")
        with self.assertRaisesRegex(RuntimeError, "changed refs/tags/v1"):
            self.sync()

    def test_fetch_failure_preserves_refs(self):
        before = self.sync()["refs"]
        with self.assertRaises(RuntimeError):
            protect.sync_repo(self.destination, self.config, str(self.root / "missing"), fetch_lfs=False)
        self.assertEqual(self.published(), before)

    def test_failed_snapshot_prevents_publication(self):
        before = self.sync()["refs"]
        self.commit("second")
        real_git = protect.git
        def fail_bundle(repo, *args, **kwargs):
            if args[:2] == ("bundle", "verify"):
                raise RuntimeError("verification failed")
            return real_git(repo, *args, **kwargs)
        with patch.object(protect, "git", side_effect=fail_bundle):
            with self.assertRaisesRegex(RuntimeError, "verification failed"):
                self.sync()
        self.assertEqual(self.published(), before)

    def test_candidate_refs_are_not_advertised(self):
        self.sync()
        repo = self.destination / "git/example.git"
        visible = subprocess.check_output(["git", "ls-remote", str(repo)]).decode()
        self.assertNotIn("refs/candidate/", visible)

    def test_missing_required_commit_blocks_bootstrap(self):
        self.config["required_commit"] = "0" * 40
        with self.assertRaises(RuntimeError): self.sync()
        self.assertEqual(self.published(), {})

    def test_authenticated_http_clone_preserves_all_branches(self):
        for i in range(25): self.run_git("branch", "topic/branch-" + str(i))
        expected = self.sync()["refs"]
        config = {"repositories": [self.config], "owner": "owner"}
        state = protect.Protection(self.destination, config)
        with patch.dict(os.environ, {"GUARD_TOKEN": "test-token"}):
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), protect.handler(state))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            try:
                header = "Authorization: Basic " + base64.b64encode(b"dr:test-token").decode()
                copy = self.root / "http-clone.git"
                result = subprocess.run(["git", "-c", "http.extraHeader=" + header,
                                         "clone", "--mirror", "http://127.0.0.1:" + str(server.server_port) + "/git/example.git", str(copy)],
                                        capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(protect.refs(copy, "refs/heads/"), expected)
            finally:
                server.shutdown()

    def test_destination_snapshot_clone_authentication_and_read_only_scope(self):
        expected = self.sync()["refs"]
        state = protect.Protection(self.destination, {"repositories": [self.config], "owner": "owner"})
        upstream_requests = []

        class Destination(protect.handler(state)):
            def authenticated(self):
                return self.headers.get("Authorization") == "Basic " + base64.b64encode(b"oauth2:test-token").decode()

            def dispatch(self):
                upstream_requests.append(self.path)
                self.path = self.path.replace("/owner/example.git/", "/git/example.git/", 1)
                return super().dispatch()

        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Destination)
        proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), protect.handler(state))
        for server in (upstream, proxy):
            self.addCleanup(server.server_close)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:" + str(proxy.server_port)
        try:
            with patch.dict(os.environ, {"GITEA_URL": "http://127.0.0.1:" + str(upstream.server_port)}):
                with self.assertRaises(urllib.error.HTTPError) as unauthenticated:
                    urllib.request.urlopen(base + "/owner/example.git/info/refs?service=git-upload-pack")
                self.assertEqual(unauthenticated.exception.code, 401)
                self.assertIn("Basic", unauthenticated.exception.headers["WWW-Authenticate"])
                unauthenticated.exception.close()
                copy = self.root / "snapshot.git"
                clone_url = base.replace("http://", "http://oauth2:test-token@") + "/owner/example.git"
                result = subprocess.run(["git", "clone", "--mirror", clone_url, str(copy)],
                                        capture_output=True, timeout=20,
                                        env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(protect.refs(copy, "refs/heads/"), expected)
                count = len(upstream_requests)
                for path, method, code in [
                    ("/owner/other.git/info/refs?service=git-upload-pack", "GET", 404),
                    ("/elsewhere/example.git/info/refs?service=git-upload-pack", "GET", 404),
                    ("/owner/example.git/info/refs?service=git-receive-pack", "GET", 403),
                    ("/owner/example.git/git-receive-pack", "POST", 403),
                    ("/owner/example.git/info/refs?service=git-upload-pack", "DELETE", 403),
                ]:
                    with self.subTest(path=path, method=method):
                        request = urllib.request.Request(base + path, method=method)
                        with self.assertRaises(urllib.error.HTTPError) as blocked:
                            urllib.request.urlopen(request)
                        self.assertEqual(blocked.exception.code, code)
                        blocked.exception.close()
                self.assertEqual(len(upstream_requests), count)
        finally:
            proxy.shutdown()
            upstream.shutdown()


if __name__ == "__main__": unittest.main()
