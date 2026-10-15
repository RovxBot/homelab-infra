#!/usr/bin/env python3
"""Export a fixed Last.fm history window in Koito's recenttracks JSON format."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://ws.audioscrobbler.com/2.0/"
TRANSIENT_API_ERRORS = {8, 11, 16, 29}
TRANSIENT_HTTP_ERRORS = {429, 500, 502, 503, 504}


def fetch_page(user, api_key, page, before, after=None):
    params = {
        "method": "user.getRecentTracks",
        "user": user,
        "api_key": api_key,
        "format": "json",
        "limit": 200,
        "page": page,
        "to": before,
        "extended": 0,
    }
    if after is not None:
        params["from"] = after
    request = Request(
        API_URL + "?" + urlencode(params),
        headers={"User-Agent": "homelab-infra-koito-export/1.0"},
    )
    for attempt in range(6):
        try:
            with urlopen(request, timeout=60) as response:
                payload = json.load(response)
        except HTTPError as error:
            if error.code not in TRANSIENT_HTTP_ERRORS:
                # urllib exceptions include the URL, which contains the key.
                raise RuntimeError(f"Last.fm returned HTTP {error.code}") from None
            payload = None
        except (URLError, TimeoutError):
            payload = None

        if payload is not None:
            if "error" not in payload:
                return payload["recenttracks"]
            code = int(payload["error"])
            if code not in TRANSIENT_API_ERRORS:
                raise RuntimeError(f"Last.fm returned API error {code}")
        if attempt < 5:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Last.fm page {page} failed after six attempts")


def export_history(user, api_key, output, before, after=None):
    output = Path(output)
    if output.exists():
        raise RuntimeError("Output already exists; choose a new filename")
    if "recenttracks" not in output.name or output.suffix != ".json":
        raise RuntimeError("Output filename must contain recenttracks and end in .json")

    temporary_path = None
    count = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent,
            prefix=".lastfm-export-", suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write("[")
            page = 1
            total_pages = None
            total = None
            while total_pages is None or page <= total_pages:
                if page > 1:
                    time.sleep(1)
                recent = fetch_page(user, api_key, page, before, after)
                attributes = recent["@attr"]
                returned_page = int(attributes["page"])
                returned_pages = max(1, int(attributes["totalPages"]))
                returned_total = int(attributes["total"])
                if returned_page != page or returned_total < 0:
                    raise RuntimeError("Last.fm returned invalid pagination metadata")
                if total_pages is None:
                    total_pages, total = returned_pages, returned_total
                elif (returned_pages, returned_total) != (total_pages, total):
                    raise RuntimeError("History changed during export; retry with the same cutoff")
                tracks = recent.get("track", [])
                if isinstance(tracks, dict):
                    tracks = [tracks]
                completed = []
                for track in tracks:
                    if track.get("@attr", {}).get("nowplaying") == "true":
                        continue
                    if not track.get("date", {}).get("uts"):
                        raise RuntimeError("A completed scrobble has no timestamp")
                    completed.append(track)
                if page > 1:
                    handle.write(",")
                json.dump({"track": completed}, handle, ensure_ascii=False)
                count += len(completed)
                print(f"Page {page}/{total_pages}: {count}/{total} scrobbles", file=sys.stderr)
                page += 1
            if count != total:
                raise RuntimeError(f"Incomplete export: expected {total} scrobbles, received {count}")
            handle.write("]\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Publish only a complete file, and never replace an existing export.
        os.link(temporary_path, output)
        return count
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api-key-file", type=Path,
                        help="File containing only the API key; otherwise use LASTFM_API_KEY")
    parser.add_argument("--before-unix", type=int, default=int(time.time()),
                        help="Fixed Last.fm 'to' cutoff (default: now)")
    parser.add_argument("--after-unix", type=int,
                        help="Last.fm 'from' cutoff for recovering a history gap")
    args = parser.parse_args()
    try:
        api_key = (args.api_key_file.read_text().strip() if args.api_key_file
                   else os.environ.get("LASTFM_API_KEY", "").strip())
        if not api_key:
            raise RuntimeError("Set LASTFM_API_KEY or provide --api-key-file")
        if args.before_unix <= 0 or (args.after_unix is not None and
                                   not 0 <= args.after_unix < args.before_unix):
            raise RuntimeError("History cutoffs must define a valid positive time window")
        print(f"Export cutoff: {args.before_unix}", file=sys.stderr)
        count = export_history(args.user, api_key, args.output,
                               args.before_unix, args.after_unix)
        print(f"Exported {count} scrobbles to {args.output}")
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as error:
        # Do not print HTTP exceptions, request URLs, or credential values.
        print(f"Export failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
