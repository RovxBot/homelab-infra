import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse


SCRIPT = Path(__file__).resolve().parents[1] / "koito-export-lastfm.py"
SPEC = importlib.util.spec_from_file_location("koito_export_lastfm", SCRIPT)
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def response(page, tracks, pages=2, total=2):
    return {"@attr": {"page": str(page), "totalPages": str(pages), "total": str(total)},
            "track": tracks}


class LastfmExportTests(unittest.TestCase):
    def test_all_pages_keep_timestamps_and_skip_now_playing(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "rovbot-recenttracks.json"
            first = {"name": "First", "artist": {"#text": "Artist"}, "date": {"uts": "100"}}
            second = {"name": "Second", "date": {"uts": "90"}}
            pages = [response(1, [first, {"@attr": {"nowplaying": "true"}}]),
                     response(2, second)]
            with patch.object(exporter, "fetch_page", side_effect=pages) as fetch, \
                    patch.object(exporter.time, "sleep"):
                self.assertEqual(exporter.export_history("rovbot", "test-key", output, 101), 2)
            self.assertEqual(json.loads(output.read_text()), [{"track": [first]}, {"track": [second]}])
            self.assertEqual([call.args[3] for call in fetch.call_args_list], [101, 101])

    def test_incomplete_export_never_publishes_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "recenttracks.json"
            with patch.object(exporter, "fetch_page", return_value=response(1, [], pages=1)), \
                    self.assertRaisesRegex(RuntimeError, "Incomplete export"):
                exporter.export_history("rovbot", "test-key", output, 101)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_changed_history_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "recenttracks.json"
            pages = [response(1, [{"date": {"uts": "100"}}]), response(2, [], total=1)]
            with patch.object(exporter, "fetch_page", side_effect=pages), \
                    patch.object(exporter.time, "sleep"), \
                    self.assertRaisesRegex(RuntimeError, "History changed"):
                exporter.export_history("rovbot", "test-key", output, 101)
            self.assertFalse(output.exists())

    def test_existing_export_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "recenttracks.json"
            output.write_text("existing history")
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                exporter.export_history("rovbot", "test-key", output, 101)
            self.assertEqual(output.read_text(), "existing history")

    def test_rate_limit_retries_and_passes_the_history_window(self):
        payload = {"recenttracks": response(1, [], pages=0, total=0)}
        with patch.object(exporter, "urlopen", side_effect=[
            HTTPError("https://example.invalid", 429, "rate limited", None, None),
            io.BytesIO(json.dumps(payload).encode()),
        ]) as opening, patch.object(exporter.time, "sleep") as sleeping:
            self.assertEqual(exporter.fetch_page("rovbot", "test-key", 1, 101, 50), payload["recenttracks"])
        query = parse_qs(urlparse(opening.call_args.args[0].full_url).query)
        self.assertEqual(query["to"], ["101"])
        self.assertEqual(query["from"], ["50"])
        sleeping.assert_called_once_with(1)

    def test_permanent_http_error_does_not_expose_the_api_key(self):
        with patch.object(exporter, "urlopen", side_effect=HTTPError(
            "https://example.invalid?api_key=sensitive", 403, "forbidden", None, None,
        )), self.assertRaisesRegex(RuntimeError, "HTTP 403") as caught:
            exporter.fetch_page("rovbot", "sensitive", 1, 101)
        self.assertNotIn("sensitive", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
