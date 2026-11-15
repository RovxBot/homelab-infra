from pathlib import Path
import importlib.util
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "check_immich_versions.py"
spec = importlib.util.spec_from_file_location("check_immich_versions", SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class ImmichVersionTests(unittest.TestCase):
    def validate(self, server="v3.4.0", ml="v3.4.0", digest="a" * 64):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "apps/immich"
            app.mkdir(parents=True)
            for component, filename, version, sha in [
                ("server", "server.yaml", server, digest),
                ("machine-learning", "machine-learning.yaml", ml, "b" * 64),
            ]:
                (app / filename).write_text(f"image: ghcr.io/immich-app/immich-{component}:{version}@sha256:{sha}\n")
            return check.check_versions(root)

    def test_equal_tags_allow_different_image_digests(self):
        self.assertEqual(self.validate(), "v3.4.0")

    def test_partial_release_fails(self):
        with self.assertRaisesRegex(ValueError, "release mismatch"):
            self.validate(ml="v3.4.1")

    def test_floating_unstable_or_unpinned_images_fail(self):
        for version, digest in [("release", "a" * 64), ("v3.4.1-rc.1", "a" * 64), ("v3.4.0", "")]:
            with self.subTest(version=version, digest=digest), self.assertRaises(ValueError):
                self.validate(server=version, digest=digest)

    def test_repository_releases_match(self):
        check.check_versions(SCRIPT.parents[2])
