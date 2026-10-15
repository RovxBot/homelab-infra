import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "koito-audit-lastfm.py"
SPEC = importlib.util.spec_from_file_location("koito_audit_lastfm", SCRIPT)
auditor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auditor)


def source(title="Song", artist="Artist", mbid=""):
    return {"date": {"uts": "100"}, "name": title,
            "artist": {"#text": artist}, "mbid": mbid}


def listen(title="Song", artist="Artist", mbid=None):
    return {"listened_at": "1970-01-01T00:01:40Z",
            "track": {"mbid": mbid, "aliases": [{"alias": title}]},
            "artists": [{"mbid": None, "aliases": [{"alias": artist}]}]}


def audit(sources, listens):
    return auditor.audit_history([{"track": sources}], {"listens": listens})


class LastfmAuditTests(unittest.TestCase):
    def test_distinct_tracks_at_the_same_timestamp_are_all_checked(self):
        result = audit([source("One"), source("Two")], [listen("One")])
        self.assertEqual(result["summary"]["matched_source_records"], 1)
        self.assertEqual(result["summary"]["missing_source_records"], 1)
        self.assertEqual(result["missing"][0]["title"], "Two")

    def test_aliases_and_case_changes_preserve_the_listen(self):
        result = audit([source("SONG", "artist")], [listen()])
        self.assertEqual(result["summary"]["missing_source_records"], 0)

    def test_two_source_entries_cannot_both_match_one_koito_record(self):
        result = audit([source(mbid="id-one"), source(artist="Collaborator", mbid="id-one")],
                       [listen(mbid="id-one")])
        self.assertEqual(result["summary"]["matched_source_records"], 1)
        self.assertEqual(result["summary"]["collapsed_recording_duplicates"], 1)
        self.assertEqual(result["summary"]["missing_source_records"], 0)

    def test_matching_reassigns_an_ambiguous_alias_to_avoid_false_missing_records(self):
        broad = listen("A")
        broad["track"]["aliases"].append({"alias": "B"})
        result = audit([source("A"), source("B")], [broad, listen("A")])
        self.assertEqual(result["summary"]["matched_source_records"], 2)
        self.assertEqual(result["summary"]["missing_source_records"], 0)

    def test_new_koito_activity_is_reported_separately(self):
        result = audit([source()], [listen(), listen("New track")])
        self.assertEqual(result["summary"]["missing_source_records"], 0)
        self.assertEqual(result["summary"]["unmatched_koito_records"], 1)

    def test_featured_credit_matches_all_separate_contributors(self):
        destination = listen(artist="Edward Maya")
        destination["artists"] += [listen(artist=name)["artists"][0] for name in ["Jigulina", "Vika"]]
        result = audit([source(artist="Edward Maya feat. Jigulina, Vika")], [destination])
        self.assertEqual(result["summary"]["missing_source_records"], 0)

    def test_featured_credit_requires_every_named_contributor(self):
        result = audit([source(artist="Artist feat. Guest")], [listen()])
        self.assertEqual(result["summary"]["missing_source_records"], 1)

    def test_complete_direct_check_recovers_an_export_omission_without_duplicates(self):
        original = {"listens": [listen("One")]}
        checks = [{"unix": 100, "data": {
            "has_next_page": False, "current_page": 1, "total_record_count": 2,
            "items": [{"time": "1970-01-01T00:01:40Z", "track": {
                "title": title, "artists": [{"name": "Artist"}]}} for title in ["One", "Two"]],
        }}]
        recovered = auditor.with_listen_checks(original, checks)
        self.assertEqual(len(original["listens"]), 1)
        result = auditor.audit_history([{"track": [source("One"), source("Two")]}], recovered)
        self.assertEqual(result["summary"]["matched_source_records"], 2)
        self.assertEqual(result["summary"]["koito_records"], 2)

    def test_partial_or_wrong_timestamp_direct_checks_are_rejected(self):
        base = {"has_next_page": False, "current_page": 1, "total_record_count": 0, "items": []}
        invalid = [dict(base, has_next_page=True), dict(base, current_page=2),
                   dict(base, total_record_count=1), dict(base, total_record_count=1, items=[{
                       "time": "1970-01-01T00:01:41Z", "track": {"title": "Song", "artists": []}}])]
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(ValueError):
                auditor.with_listen_checks({"listens": []}, [{"unix": 100, "data": response}])


if __name__ == "__main__":
    unittest.main()
