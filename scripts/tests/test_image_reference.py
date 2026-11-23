import unittest

from scripts.ci.image_reference import pinned_release, release_tag
from scripts.ci.check_kubescape_recurring import check_runner_images


class ReleaseReferenceTests(unittest.TestCase):
    def test_recurring_python_patches_and_digests_preserve_the_runtime_contract(self):
        for tag in ('3.15-alpine', '3.15.1-alpine'):
            for digest in ('a', 'b'):
                image = 'python:' + tag + '@sha256:' + digest * 64
                check_runner_images({'containers': [{'image': image}], 'initContainers': [{'image': image}]})
        for image in ('python:3.16-alpine@sha256:' + 'a' * 64,
                      'other/python:3.15-alpine@sha256:' + 'a' * 64,
                      'python:3.15-alpine', 'python:3.15-slim@sha256:' + 'a' * 64):
            with self.subTest(image=image), self.assertRaises(ValueError):
                check_runner_images({'containers': [{'image': image}], 'initContainers': []})
        with self.assertRaises(AssertionError):
            check_runner_images({'containers': [{'image': 'python:3.15-alpine@sha256:' + 'a' * 64}],
                                 'initContainers': [{'image': 'python:3.15-alpine@sha256:' + 'b' * 64}]})
    def test_patch_tags_allow_distinct_digests(self):
        for patch in (0, 1, 12):
            self.assertEqual(pinned_release(
                f'example/app:0.21.{patch}@sha256:' + str(patch % 10) * 64,
                repository='example/app', minimum=(0, 21, 0), release_line=(0, 21)), (0, 21, patch))

    def test_gatus_support_boundary_allows_later_compatible_releases(self):
        for tag in ('v5.34.0', 'v5.34.1', 'v5.35.0'):
            release_tag(tag, minimum=(5, 34, 0), release_line=(5,))
        for tag in ('v5.33.9', 'v6.0.0', 'latest', 'v5.34.1-rc1'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release_tag(tag, minimum=(5, 34, 0), release_line=(5,))
