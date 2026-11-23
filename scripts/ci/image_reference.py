"""Property checks for reviewed stable image release lines, not copied pins."""
import re


def pinned_image_tag(image, *, repository, tag_pattern):
    match = re.fullmatch(re.escape(repository) + r':(' + tag_pattern + r')@sha256:[a-f0-9]{64}', image)
    if not match:
        raise ValueError('expected the upstream repository, reviewed tag and immutable SHA256 digest')
    return match[1]


def release_tag(tag, *, minimum, release_line):
    match = re.fullmatch(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', tag)
    if not match:
        raise ValueError('expected a stable semantic release tag')
    version = tuple(map(int, match.groups()))
    if version < minimum or version[:len(release_line)] != release_line:
        raise ValueError('image is outside the reviewed compatibility boundary')
    return version


def pinned_release(image, *, repository, minimum, release_line):
    tag = pinned_image_tag(image, repository=repository, tag_pattern=r'[^@]+')
    return release_tag(tag, minimum=minimum, release_line=release_line)
