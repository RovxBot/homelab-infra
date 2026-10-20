"""Read-only BirdNET v2 database and clip service for Home Assistant.

No BirdNET settings, identities, source URLs or database writes are exposed.
The volume is also mounted read-only; mode=ro and query_only add independent
guards. Date boundaries are supplied by HA using its actual station timezone.
"""

from __future__ import annotations

from contextlib import closing
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit


class BadRequest(ValueError):
    """Invalid dashboard filter."""


def integer(value, minimum, maximum):
    try:
        number = int(value)
    except (ValueError, TypeError) as err:
        raise BadRequest("Invalid numeric filter") from err
    if not minimum <= number <= maximum:
        raise BadRequest("Numeric filter outside allowed range")
    return number


def byte_range(header, size):
    """Resolve one HTTP byte range, rejecting malformed and excessive ranges."""
    if not header:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or not any(match.groups()) or size == 0:
        raise BadRequest("Invalid audio range")
    left, right = match.groups()
    if not left:
        suffix = integer(right, 1, 2**63 - 1)
        return max(0, size - suffix), size - 1, True
    start = integer(left, 0, size - 1)
    end = min(integer(right, start, 2**63 - 1), size - 1) if right else size - 1
    return start, end, True


class BirdDatabase:
    def __init__(self, database, clips, models):
        self.database = Path(database)
        self.clips = Path(clips).resolve()
        self.names = {}
        # Use the existing downloaded catalog, without mounting the SSO config.
        for path in sorted(Path(models).glob("shared/*labels.txt")):
            for line in path.read_text().splitlines():
                scientific, separator, common = line.partition("_")
                if separator:
                    self.names[scientific] = common

    def connect(self):
        db = sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.create_function("bird_name", 1, lambda name: self.names.get(name, name), deterministic=True)
        return db

    def ready(self):
        with closing(self.connect()) as db:
            db.execute("SELECT id FROM detections LIMIT 1").fetchall()

    def dashboard(self, query):
        def get(key, default=""):
            values = query.get(key, [default])
            if len(values) != 1:
                raise BadRequest("Repeated query parameter")
            return values[0]

        if set(query) - {"start", "end", "source", "confidence", "search", "offset", "rejected"}:
            raise BadRequest("Unknown dashboard filter")
        start = integer(get("start"), 946684800, 4102444800)
        end = integer(get("end"), start + 1, start + 27 * 3600)
        offset = integer(get("offset", "0"), 0, 1000000)
        try:
            confidence = float(get("confidence", "0"))
        except (ValueError, TypeError) as err:
            raise BadRequest("Invalid confidence") from err
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise BadRequest("Invalid confidence")
        search = get("search").strip()
        if len(search) > 80:
            raise BadRequest("Search is too long")
        rejected = get("rejected", "0")
        if rejected not in ("0", "1"):
            raise BadRequest("Invalid review filter")

        where = ["d.detected_at >= ?", "d.detected_at < ?", "d.confidence >= ?"]
        parameters = [start, end, confidence]
        if rejected != "1":
            where.append("COALESCE(r.verified, '') != 'false_positive'")
        source = get("source")
        if source:
            where.append("d.source_id = ?")
            parameters.append(integer(source, 1, 2**31 - 1))
        if search:
            where.append("(instr(lower(l.scientific_name), ?) > 0 OR instr(lower(bird_name(l.scientific_name)), ?) > 0)")
            parameters.extend([search.lower(), search.lower()])
        joined = """FROM detections d JOIN labels l ON l.id=d.label_id
                    LEFT JOIN audio_sources s ON s.id=d.source_id
                    LEFT JOIN detection_reviews r ON r.detection_id=d.id"""
        filtered = joined + " WHERE " + " AND ".join(where)
        with closing(self.connect()) as db:
            # All totals and rows represent the same short read snapshot.
            db.execute("BEGIN")
            totals = dict(db.execute("""SELECT count(*) AS detections,
                count(DISTINCT l.scientific_name) AS species,
                max(d.confidence) AS highest_confidence, max(d.detected_at) AS latest
                """ + filtered, parameters).fetchone())
            hourly = [0] * math.ceil((end - start) / 3600)
            for row in db.execute("SELECT CAST((d.detected_at - ?) / 3600 AS INTEGER) AS hour, count(*) AS count "
                                  + filtered + " GROUP BY hour", [start, *parameters]):
                hourly[row["hour"]] = row["count"]
            images = {}
            for row in db.execute("""SELECT l.scientific_name, i.url, i.author_name, i.author_url,
                    i.license_name, i.license_url FROM image_caches i JOIN labels l ON l.id=i.label_id
                    ORDER BY i.cached_at DESC, i.id DESC"""):
                images.setdefault(row["scientific_name"], dict(row))
            species = []
            for row in db.execute("""SELECT l.scientific_name, count(*) AS count,
                    min(d.detected_at) AS first, max(d.detected_at) AS last,
                    max(d.confidence) AS confidence """ + filtered
                                  + " GROUP BY l.scientific_name ORDER BY count DESC, l.scientific_name", parameters):
                item = dict(row)
                item["common_name"] = self.names.get(item["scientific_name"], item["scientific_name"])
                item["image"] = images.get(item["scientific_name"])
                species.append(item)
            detections = []
            for row in db.execute("""SELECT d.id, d.detected_at, d.confidence, l.scientific_name,
                    s.display_name AS source, d.source_id,
                    COALESCE(r.verified, 'unverified') AS review,
                    d.clip_name, m.name AS model, m.version AS model_version """
                                  + filtered.replace(" WHERE ", " LEFT JOIN ai_models m ON m.id=d.model_id WHERE ")
                                  + " ORDER BY d.detected_at DESC, d.id DESC LIMIT 24 OFFSET ?", [*parameters, offset]):
                item = dict(row)
                clip = item.pop("clip_name")
                item["has_audio"] = self.clip_path(clip) is not None
                item["common_name"] = self.names.get(item["scientific_name"], item["scientific_name"])
                item["image"] = images.get(item["scientific_name"])
                detections.append(item)
            # Migrated RTSP sources can be tagged 'unknown' in v2. Use the
            # sources actually referenced by detections, without exposing URIs.
            sources = [dict(row) for row in db.execute("""SELECT DISTINCT s.id, s.display_name AS name
                FROM audio_sources s JOIN detections d ON d.source_id=s.id ORDER BY s.display_name""")]
        return {"totals": totals, "hourly": hourly, "species": species,
                "detections": detections, "sources": sources, "offset": offset,
                "has_more": offset + len(detections) < totals["detections"]}

    def clip_path(self, name):
        if not name:
            return None
        candidate = (self.clips / name).resolve()
        if not candidate.is_relative_to(self.clips) or candidate.suffix.lower() not in (".flac", ".wav", ".mp3", ".ogg", ".opus"):
            return None
        return candidate if candidate.is_file() else None

    def audio(self, detection_id):
        with closing(self.connect()) as db:
            row = db.execute("SELECT clip_name FROM detections WHERE id=?", [detection_id]).fetchone()
        return self.clip_path(row[0]) if row else None


def make_handler(database, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            # Avoid logging access tokens or signed paths.
            pass

        def json(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.connection.settimeout(15)
            try:
                self.handle_get()
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            except BadRequest as err:
                self.json(400, {"error": str(err)})
            except (sqlite3.Error, OSError):
                self.json(503, {"error": "BirdNET data is temporarily unavailable"})

        def handle_get(self):
            if len(self.path) > 2048:
                raise BadRequest("Request is too long")
            path = urlsplit(self.path)
            if path.path == "/health":
                database.ready()
                return self.json(200, {"ready": True})
            if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + token).encode()):
                return self.json(401, {"error": "Authentication required"})
            if path.path == "/v1/dashboard":
                return self.json(200, database.dashboard(parse_qs(path.query, keep_blank_values=True)))
            match = re.fullmatch(r"/v1/audio/([1-9][0-9]{0,18})", path.path)
            if not match or path.query:
                return self.json(404, {"error": "Not found"})
            clip = database.audio(int(match[1]))
            if not clip:
                return self.json(404, {"error": "Recording no longer available"})
            with clip.open("rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                try:
                    start, end, partial = byte_range(self.headers.get("Range"), size)
                except BadRequest:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                self.send_response(206 if partial else 200)
                self.send_header("Content-Type", mimetypes.guess_type(clip.name)[0] or "audio/flac")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "private, max-age=60")
                self.send_header("X-Content-Type-Options", "nosniff")
                if partial:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                stream.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def do_POST(self):
            self.json(405, {"error": "Read-only service"})

    return Handler


def main():
    token = Path(os.environ["TOKEN_FILE"]).read_text().strip()
    if len(token) < 32:
        raise ValueError("Dashboard token must have at least 32 characters")
    database = BirdDatabase(os.environ["DATABASE_PATH"], os.environ["CLIPS_PATH"], os.environ["MODELS_PATH"])
    server = ThreadingHTTPServer(("0.0.0.0", 8090), make_handler(database, token))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
