#!/usr/bin/env python3
"""Split a Last.fm export into bounded native Koito imports with checkpoints."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


def write_private_json(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")


def prepare_batches(source, output_directory, batch_size=500):
    source, output_directory = Path(source), Path(output_directory)
    if batch_size < 1:
        raise ValueError("Batch size must be positive")
    if output_directory.exists():
        raise ValueError("Output directory already exists; choose a new directory")
    original = source.read_bytes()
    pages = json.loads(original)
    if not isinstance(pages, list):
        raise ValueError("Expected an array of Last.fm export pages")
    tracks = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("track"), list):
            raise ValueError("Every export page must contain a track array")
        for track in page["track"]:
            if (not isinstance(track, dict) or not track.get("name") or
                    not track.get("artist", {}).get("#text") or
                    int(track.get("date", {}).get("uts", 0)) <= 0):
                raise ValueError("Every scrobble needs an artist, title and positive timestamp")
            tracks.append(track)
    manifest = {
        "source_sha256": hashlib.sha256(original).hexdigest(),
        "records": len(tracks),
        "batch_size": batch_size,
        "files": [],
    }
    temporary = Path(tempfile.mkdtemp(prefix=".lastfm-batches-", dir=output_directory.parent))
    try:
        for index, start in enumerate(range(0, len(tracks), batch_size), 1):
            chunk = tracks[start:start + batch_size]
            name = f"lastfm-recenttracks-part-{index:05d}.json"
            path = temporary / name
            write_private_json(path, [{"track": chunk}])
            manifest["files"].append({
                "name": name,
                "records": len(chunk),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        assert sum(batch["records"] for batch in manifest["files"]) == len(tracks)
        write_private_json(temporary / "manifest.json", manifest)
        temporary.rename(output_directory)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    manifest = prepare_batches(args.source, args.output_directory, args.batch_size)
    print(f"Prepared {manifest['records']} scrobbles in {len(manifest['files'])} batches")


if __name__ == "__main__":
    main()
