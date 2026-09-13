"""Synthetic cache replacement failures; no collection or provider access."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import getall


class Response:
    def __init__(self, data):
        self.data = data
        self.closed = False

    def raise_for_status(self):
        pass

    def json(self):
        return self.data

    def close(self):
        self.closed = True


class CacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='pokemon-cache-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / 'cache.json'
        for name, path in [('SETS_CACHE_FILE', self.cache),
                           ('DOWNLOADED_SETS_CSV', self.root / 'sets.csv'),
                           ('DOWNLOADED_CARDS_CSV', self.root / 'cards.csv')]:
            self.enterContext(patch.object(getall, name, str(path)))
        self.enterContext(patch('requests.sessions.Session.request', side_effect=AssertionError('Provider access forbidden')))
        self.enterContext(redirect_stdout(io.StringIO()))
        self.original = b'{"data":[{"id":"kept","name":"Original","printedTotal":2}],"unknown":{"keep":true}}\n'
        self.cache.write_bytes(self.original)
        self.downloader = getall.PokemonCardDownloader()
        self.fresh = {'data': [{'id': 'fresh', 'name': 'New', 'printedTotal': 3}], 'unknown': {'number': 900719925474099312345}}

    def fetch(self, data=None):
        response = Response(self.fresh if data is None else data)
        with patch.object(getall.requests, 'get', return_value=response):
            result = self.downloader._fetch_all_sets_from_api()
        return result, response

    def test_serialization_failure_preserves_last_good_cache(self):
        def interrupted_dump(data, stream, **kwargs):
            stream.write('{"data":[')
            raise OSError('Synthetic disk failure')
        with patch.object(getall.json, 'dump', side_effect=interrupted_dump):
            result, _ = self.fetch()
        self.assertIsNone(result)
        self.assertEqual(self.cache.read_bytes(), self.original)

    def test_fsync_failure_preserves_last_good_cache(self):
        with patch.object(getall.os, 'fsync', side_effect=OSError('Synthetic sync failure')):
            result, _ = self.fetch()
        self.assertIsNone(result)
        self.assertEqual(self.cache.read_bytes(), self.original)

    def test_replacement_failure_preserves_last_good_cache(self):
        with patch.object(getall.os, 'replace', side_effect=PermissionError('Synthetic sharing conflict')):
            result, _ = self.fetch()
        self.assertIsNone(result)
        self.assertEqual(self.cache.read_bytes(), self.original)

    def test_invalid_provider_catalog_never_replaces_cache(self):
        for data in [{}, {'data': []}, {'data': {}}, {'data': [None]}, {'data': [{'id': 'missing-name'}]},
                     {'data': [{'id': 'same', 'name': 'One'}, {'id': 'same', 'name': 'Two'}]},
                     {'data': [{'id': 'negative', 'name': 'Bad', 'printedTotal': -1}]}]:
            with self.subTest(data=data):
                result, _ = self.fetch(data)
                self.assertIsNone(result)
                self.assertEqual(self.cache.read_bytes(), self.original)

    def test_bad_existing_cache_fails_closed_without_fetch_or_memory_loss(self):
        self.cache.write_bytes(b'{"data":[')
        self.downloader.api_sets_data = {'kept': {'id': 'kept', 'name': 'Original'}}
        with patch.object(getall.requests, 'get', side_effect=AssertionError('Do not overwrite an unreadable cache')) as request:
            self.assertFalse(self.downloader._load_or_fetch_sets())
        request.assert_not_called()
        self.assertEqual(self.cache.read_bytes(), b'{"data":[')
        self.assertEqual(list(self.downloader.api_sets_data), ['kept'])

    def test_valid_cache_loads_without_network(self):
        with patch.object(getall.requests, 'get', side_effect=AssertionError('Cache hit must not fetch')):
            self.assertTrue(self.downloader._load_or_fetch_sets())
        self.assertEqual(list(self.downloader.api_sets_data), ['kept'])

    def test_successful_refresh_retains_unknown_fields_and_closes_response(self):
        result, response = self.fetch()
        self.assertEqual(result, self.fresh['data'])
        self.assertEqual(json.loads(self.cache.read_text()), self.fresh)
        self.assertTrue(response.closed)

    def test_old_bytes_remain_visible_until_complete_replacement(self):
        replace = getall.os.replace
        def observe(source, destination):
            self.assertEqual(self.cache.read_bytes(), self.original)
            self.assertEqual(json.loads(Path(source).read_text()), self.fresh)
            replace(source, destination)
        with patch.object(getall.os, 'replace', side_effect=observe):
            self.assertEqual(self.fetch()[0], self.fresh['data'])
        self.assertEqual(list(self.root.glob('.*.tmp')), [])

    def test_interruption_preserves_cache_and_closes_response(self):
        response = Response(self.fresh)
        with patch.object(getall.requests, 'get', return_value=response), \
             patch.object(getall.json, 'dump', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.downloader._fetch_all_sets_from_api()
        self.assertTrue(response.closed)
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertEqual(list(self.root.glob('.*.tmp')), [])

    def test_failed_first_cache_write_leaves_no_visible_partial_file(self):
        getall.SETS_CACHE_FILE = str(self.root / 'new-cache.json')
        with patch.object(getall.os, 'replace', side_effect=OSError('Synthetic failure')):
            self.assertIsNone(self.fetch()[0])
        self.assertFalse(Path(getall.SETS_CACHE_FILE).exists())
        self.assertEqual(list(self.root.glob('.*.tmp')), [])

    def test_failed_forced_refresh_keeps_previous_in_memory_catalogue(self):
        self.downloader.api_sets_data = {'kept': {'id': 'kept', 'name': 'Original'}}
        with patch.object(getall.requests, 'get', return_value=Response(self.fresh)), \
             patch.object(getall.os, 'replace', side_effect=PermissionError('Synthetic sharing failure')):
            self.assertFalse(self.downloader._load_or_fetch_sets(force_update=True))
        self.assertEqual(list(self.downloader.api_sets_data), ['kept'])
        self.assertEqual(self.cache.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
