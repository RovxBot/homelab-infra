#!/usr/bin/env python3
"""Reconcile Last.fm history with a Koito JSON export, one listen at a time."""

import argparse
from collections import defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
import unicodedata


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def aliases(items):
    return {normalized(item["alias"]) for item in items or []}


def recording_id(value):
    value = (value or "").lower()
    return "" if value == "00000000-0000-0000-0000-000000000000" else value


def matches(source, destination):
    mbid = recording_id(source.get("mbid"))
    if mbid and mbid == recording_id(destination["track"].get("mbid")):
        return True
    if normalized(source["name"]) not in aliases(destination["track"].get("aliases")):
        return False
    source_artist = normalized(source["artist"]["#text"])
    artist_mbid = recording_id(source["artist"].get("mbid"))
    return any(source_artist in aliases(artist.get("aliases")) or
               (artist_mbid and artist_mbid == recording_id(artist.get("mbid")))
               for artist in destination["artists"])


def audit_history(source_pages, koito_export):
    source_by_time = defaultdict(list)
    koito_by_time = defaultdict(list)
    for page in source_pages:
        for track in page["track"]:
            source_by_time[int(track["date"]["uts"])].append(track)
    for listen in koito_export["listens"]:
        timestamp = int(datetime.fromisoformat(listen["listened_at"].replace("Z", "+00:00")).timestamp())
        koito_by_time[timestamp].append(listen)

    matched = 0
    unmatched = []
    duplicates = []
    unmatched_koito = 0
    for timestamp in source_by_time.keys() | koito_by_time.keys():
        sources = source_by_time.get(timestamp, [])
        destinations = koito_by_time.get(timestamp, [])
        edges = [[i for i, dest in enumerate(destinations) if matches(src, dest)] for src in sources]
        owner = {}

        def assign(source_index, visited):
            for index in edges[source_index]:
                if index in visited:
                    continue
                visited.add(index)
                if index not in owner or assign(owner[index], visited):
                    owner[index] = source_index
                    return True
            return False

        for index in range(len(sources)):
            assign(index, set())
        matched_sources = set(owner.values())
        matched += len(matched_sources)
        unmatched_koito += len(destinations) - len(owner)
        for index, source in enumerate(sources):
            if index in matched_sources:
                continue
            detail = {"unix": timestamp, "artist": source["artist"]["#text"],
                      "title": source["name"], "mbid": source.get("mbid", "")}
            mbid = recording_id(source.get("mbid"))
            if mbid and any(mbid == recording_id(dest["track"].get("mbid")) for dest in destinations):
                duplicates.append(detail)
            else:
                unmatched.append(detail)
    summary = {
        "source_records": sum(map(len, source_by_time.values())),
        "koito_records": len(koito_export["listens"]),
        "matched_source_records": matched,
        "collapsed_recording_duplicates": len(duplicates),
        "missing_source_records": len(unmatched),
        "unmatched_koito_records": unmatched_koito,
        "source_earliest_unix": min(source_by_time) if source_by_time else None,
        "koito_earliest_unix": min(koito_by_time) if koito_by_time else None,
    }
    return {"summary": summary, "missing": sorted(unmatched, key=lambda item: item["unix"]),
            "collapsed_duplicates": sorted(duplicates, key=lambda item: item["unix"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lastfm_export", type=Path)
    parser.add_argument("koito_export", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = audit_history(json.loads(args.lastfm_export.read_text()),
                           json.loads(args.koito_export.read_text()))
    if args.report:
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print(json.dumps(result["summary"], indent=2))
    return 1 if result["summary"]["missing_source_records"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
