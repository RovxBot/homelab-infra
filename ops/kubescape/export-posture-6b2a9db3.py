#!/usr/bin/env python3
"""Stream private history over exec stdout, then verify it locally without extraction."""
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile

LIMIT = 512 * 1024**2
MAX_MEMBERS = 20000
EXPECTED = {
    "mode": "posture",
    "scanID": "6b2a9db3-1b9b-4fba-ad3b-285b444e5fc1",
    "scannerPodUID": "42044c36-e416-4d97-bff7-cf15f83110cd",
    "submittedAt": "2026-11-26T20:13:52.336641Z",
}


def require(ok):
    if not ok:
        raise ValueError("private history verification failed")


def export(root, output):
    require(root.is_dir() and not root.is_symlink())
    # Linux flock works on a read-only fd on the Longhorn filesystem.
    fd = os.open(root / "assessment.lock", os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as lock:
        require(stat.S_ISREG(os.fstat(lock.fileno()).st_mode))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(json.loads((root / "inflight.json").read_bytes()) == EXPECTED)
        paths = [root]
        total = 0
        for directory, directories, files in os.walk(root):
            for name in sorted(directories + files):
                path = Path(directory) / name
                mode = path.lstat().st_mode
                require(stat.S_ISREG(mode) or stat.S_ISDIR(mode))
                paths.append(path)
                total += path.stat().st_size if stat.S_ISREG(mode) else 0
                require(len(paths) <= MAX_MEMBERS and total <= LIMIT)
        with tarfile.open(fileobj=output, mode="w|gz", compresslevel=1) as archive:
            for path in sorted(paths):
                archive.add(path, arcname="assessments" if path == root else
                            "assessments/" + path.relative_to(root).as_posix(), recursive=False)


def verify(path):
    names, total, intent, markers = set(), 0, None, 0
    require(path.stat().st_size <= LIMIT + 16 * 1024**2)
    with tarfile.open(path, mode="r|gz") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            require(not name.is_absolute() and ".." not in name.parts
                    and name.parts[0] == "assessments" and member.name not in names
                    and (member.isfile() or member.isdir()))
            names.add(member.name)
            total += member.size
            require(len(names) <= MAX_MEMBERS and total <= LIMIT)
            if member.isfile():
                stream = archive.extractfile(member)
                if member.name == "assessments/inflight.json":
                    require(member.size <= 65536)
                    intent = stream.read(65537)
                else:
                    while stream.read(65536):
                        pass
                markers += int(member.name.endswith(".done"))
    # Consume the complete gzip stream too, including its checksum/trailer.
    import gzip
    with gzip.open(path, "rb") as stream:
        while stream.read(65536):
            pass
    require("assessments/assessment.lock" in names and intent is not None
            and json.loads(intent) == EXPECTED)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"privateExportSHA256": digest,
            "exportedInflightSHA256": hashlib.sha256(intent).hexdigest(),
            "scanID": EXPECTED["scanID"], "members": len(names),
            "historyBytes": total, "completionMarkers": markers}


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["export"]:
            export(Path("/history"), sys.stdout.buffer)
        elif len(sys.argv) == 3 and sys.argv[1] == "verify":
            print(json.dumps(verify(Path(sys.argv[2])), sort_keys=True))
        else:
            raise ValueError("usage: export | verify PRIVATE_ARCHIVE")
    except Exception:
        # No filenames, history, API bodies or credentials in exec stderr/logs.
        print("private history export/verification failed", file=sys.stderr)
        sys.exit(1)
