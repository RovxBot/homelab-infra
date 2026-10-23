import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("update_image", Path(__file__).parents[1] / "update-image.py")
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)

REPO = "ghcr.io/rovxbot/grimguzzler-registration"
OLD = "sha256:" + "0" * 64
NEW = "sha256:" + "f" * 64


class UpdateImageTests(unittest.TestCase):
    def test_replaces_stale_digest_with_tag(self):
        original = f"  image: {REPO}:old@{OLD}\n"
        updated = updater.update_image(original, "master-new", NEW)
        self.assertEqual(updated, f"  image: {REPO}:master-new@{NEW}\n")
        self.assertNotIn(OLD, updated)

    def test_tag_only_image_gains_digest(self):
        self.assertEqual(updater.update_image(f"image: {REPO}:old\n", "new", NEW), f"image: {REPO}:new@{NEW}\n")

    def test_digest_only_image_gains_tag(self):
        self.assertEqual(updater.update_image(f"image: {REPO}@{OLD}\n", "new", NEW), f"image: {REPO}:new@{NEW}\n")

    def test_preserves_other_images_and_comments(self):
        original = f"image: ghcr.io/example/other:old@{OLD}\n  image: {REPO}:old@{OLD} # pinned\n"
        updated = updater.update_image(original, "new", NEW)
        self.assertEqual(updated, f"image: ghcr.io/example/other:old@{OLD}\n  image: {REPO}:new@{NEW} # pinned\n")

    def test_invalid_digest_is_rejected(self):
        for digest in ["", "sha256:abc", "latest", "sha256:" + "g" * 64]:
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                updater.update_image(f"image: {REPO}:old@{OLD}\n", "new", digest)

    def test_invalid_tag_is_rejected(self):
        for tag in ["", "bad tag", "bad@tag", "-bad", "a" * 129]:
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                updater.update_image(f"image: {REPO}:old@{OLD}\n", tag, NEW)

    def test_missing_image_is_rejected(self):
        with self.assertRaises(ValueError):
            updater.update_image("image: unrelated:old\n", "new", NEW)

    def test_multiple_images_are_rejected(self):
        with self.assertRaises(ValueError):
            updater.update_image(f"image: {REPO}:old\nimage: {REPO}:old\n", "new", NEW)

    def test_reapplying_same_image_is_idempotent(self):
        original = f"image: {REPO}:new@{NEW}\n"
        self.assertEqual(updater.update_image(original, "new", NEW), original)


if __name__ == "__main__":
    unittest.main()
