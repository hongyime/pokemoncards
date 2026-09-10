"""Exercise checkpoint persistence with synthetic files and blocked networking."""
import csv
from datetime import datetime
import io
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import getall


class SetMetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pokemon-metadata-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sets_path = self.root / "sets.csv"
        self.cards_path = self.root / "cards.csv"
        for name, value in (("DOWNLOADED_SETS_CSV", str(self.sets_path)),
                            ("DOWNLOADED_CARDS_CSV", str(self.cards_path)),
                            ("SETS_CACHE_FILE", str(self.root / "cache.json"))):
            replacement = patch.object(getall, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("Network access forbidden"))
        network.start()
        self.addCleanup(network.stop)
        output = redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        clock = patch.object(getall, "datetime")
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.clock.now.return_value = datetime(2026, 9, 11, 6, 0, 0)
        self.original = {
            "set_id": "fixture", "set_name": "Fixture set", "printed_total": "2",
            "last_updated": "2026/01/01", "last_checked": "2026-09-11 06:00:00",
            "cards_downloaded": "2", "set_complete": "True",
        }
        self.write_sets([self.original])
        self.cards_path.write_text(
            ",".join(getall.CARDS_HEADERS) + "\nfixture,Fixture set,1,1.png,yesterday,https://example.invalid/1.png,True,42,0.1,success\n",
            encoding="utf-8",
        )
        self.downloader = getall.PokemonCardDownloader()
        self.downloader.api_sets_data = {
            "fixture": {"id": "fixture", "name": "Fixture set", "printedTotal": 2,
                        "updatedAt": "2026/01/01"}
        }

    def write_sets(self, rows):
        with self.sets_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=getall.SETS_HEADERS)
            writer.writeheader()
            writer.writerows(rows)

    def reload(self):
        return getall.PokemonCardDownloader().downloaded_sets_data

    def assert_progress_preserved(self, row):
        self.assertEqual(row["cards_downloaded"], "2")
        self.assertEqual(row["set_complete"], "True")

    def test_existing_name_survives_restart_without_a_new_set(self):
        self.downloader.api_sets_data["fixture"]["name"] = "Pokémon, revised"
        self.downloader._populate_sets_csv()
        row = self.reload()["fixture"]
        self.assertEqual(row["set_name"], "Pokémon, revised")
        self.assert_progress_preserved(row)

    def test_existing_total_survives_restart(self):
        self.downloader.api_sets_data["fixture"]["printedTotal"] = 3
        self.downloader._populate_sets_csv()
        row = self.reload()["fixture"]
        self.assertEqual(row["printed_total"], "3")
        self.assert_progress_preserved(row)

    def test_existing_updated_at_survives_restart(self):
        self.downloader.api_sets_data["fixture"]["updatedAt"] = "2026/09/11"
        self.downloader._populate_sets_csv()
        self.assertEqual(self.reload()["fixture"]["last_updated"], "2026/09/11")

    def test_check_time_survives_restart_when_only_time_changes(self):
        self.clock.now.return_value = datetime(2026, 9, 11, 6, 5, 0)
        self.downloader._populate_sets_csv()
        self.assertEqual(self.reload()["fixture"]["last_checked"], "2026-09-11 06:05:00")

    def test_unchanged_rows_do_not_replace_the_checkpoint(self):
        before = self.sets_path.read_bytes()
        with patch.object(getall.os, "replace", wraps=getall.os.replace) as replace:
            self.downloader._populate_sets_csv()
            replace.assert_not_called()
        self.assertEqual(self.sets_path.read_bytes(), before)

    def test_mixed_new_and_existing_sets_keep_all_progress(self):
        self.downloader.api_sets_data["fixture"]["name"] = "Revised"
        self.downloader.api_sets_data["new"] = {"id": "new", "name": "New fixture", "total": 5}
        self.downloader._populate_sets_csv()
        rows = self.reload()
        self.assertEqual(set(rows), {"fixture", "new"})
        self.assertEqual(rows["fixture"]["set_name"], "Revised")
        self.assert_progress_preserved(rows["fixture"])
        self.assertEqual(rows["new"]["printed_total"], "5")
        self.assertEqual(rows["new"]["cards_downloaded"], "0")
        self.assertEqual(rows["new"]["set_complete"], "False")

    def test_unlisted_set_is_preserved(self):
        retained = dict(self.original, set_id="retained", set_name="Older fixture")
        self.write_sets([self.original, retained])
        self.downloader.downloaded_sets_data = self.reload()
        self.downloader.api_sets_data["fixture"]["name"] = "Revised"
        self.downloader._populate_sets_csv()
        self.assertEqual(self.reload()["retained"], retained)

    def test_missing_checkpoint_is_recreated_from_memory(self):
        # Only a synthetic file owned by this test is removed.
        self.sets_path.unlink()
        self.downloader._populate_sets_csv()
        self.assertEqual(self.reload()["fixture"], self.original)

    def test_missing_api_updated_at_preserves_previous_value(self):
        del self.downloader.api_sets_data["fixture"]["updatedAt"]
        self.downloader.api_sets_data["fixture"]["name"] = "Revised"
        self.downloader._populate_sets_csv()
        self.assertEqual(self.reload()["fixture"]["last_updated"], "2026/01/01")

    def test_failed_replacement_preserves_disk_checkpoint_and_stops(self):
        before = self.sets_path.read_bytes()
        self.downloader.api_sets_data["fixture"]["name"] = "Revised"
        with patch.object(getall.os, "replace", side_effect=OSError("Synthetic write failure")):
            self.downloader._populate_sets_csv()
        self.assertTrue(self.downloader.stop_script)
        self.assertEqual(self.sets_path.read_bytes(), before)

    def test_set_refresh_does_not_write_card_progress(self):
        before = self.cards_path.read_bytes()
        self.downloader.api_sets_data["fixture"]["name"] = "Revised"
        self.downloader._populate_sets_csv()
        self.assertEqual(self.cards_path.read_bytes(), before)

    def test_empty_api_result_preserves_existing_checkpoint(self):
        before = self.sets_path.read_bytes()
        self.downloader.api_sets_data = {}
        self.downloader._populate_sets_csv()
        self.assertEqual(self.sets_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
