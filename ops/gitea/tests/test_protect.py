import importlib.util
import pathlib
import subprocess
import tempfile
import unittest
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


if __name__ == "__main__": unittest.main()
