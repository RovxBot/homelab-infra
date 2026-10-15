import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "koito-prepare-lastfm-import.py"
SPEC = importlib.util.spec_from_file_location("koito_prepare_lastfm_import", SCRIPT)
importer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(importer)


def track(index):
    return {"name": f"Track {index}", "artist": {"#text": "Artist ü"},
            "album": {"#text": "Album"}, "date": {"uts": str(1000 + index)}}


class LastfmBatchTests(unittest.TestCase):
    def test_batches_preserve_every_record_and_order_across_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "recenttracks.json"
            records = [track(i) for i in range(7)]
            source.write_text(json.dumps([{"track": records[:4]}, {"track": records[4:]}]))
            output = Path(directory) / "batches"
            manifest = importer.prepare_batches(source, output, batch_size=3)
            reconstructed = []
            for batch in manifest["files"]:
                path = output / batch["name"]
                items = json.loads(path.read_text())[0]["track"]
                self.assertLessEqual(len(items), 3)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), batch["sha256"])
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertIn("recenttracks", path.name)
                reconstructed.extend(items)
            self.assertEqual(reconstructed, records)
            self.assertEqual(manifest["records"], 7)
            self.assertEqual(json.loads((output / "manifest.json").read_text()), manifest)

    def test_incomplete_scrobble_does_not_publish_any_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "recenttracks.json"
            source.write_text(json.dumps([{"track": [track(1), {"name": "Missing date"}]}]))
            output = Path(directory) / "batches"
            with self.assertRaises(ValueError):
                importer.prepare_batches(source, output)
            self.assertEqual(list(Path(directory).iterdir()), [source])

    def test_existing_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "recenttracks.json"
            source.write_text("[]")
            output = Path(directory) / "batches"
            output.mkdir()
            existing = output / "existing.json"
            existing.write_text("existing history")
            with self.assertRaises(ValueError):
                importer.prepare_batches(source, output)
            self.assertEqual(existing.read_text(), "existing history")

    def test_invalid_page_shape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "recenttracks.json"
            source.write_text('{"recenttracks":{"track":[]}}')
            with self.assertRaises(ValueError):
                importer.prepare_batches(source, Path(directory) / "batches")

    def test_zero_batch_size_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "recenttracks.json"
            source.write_text("[]")
            with self.assertRaises(ValueError):
                importer.prepare_batches(source, Path(directory) / "batches", 0)


if __name__ == "__main__":
    unittest.main()
