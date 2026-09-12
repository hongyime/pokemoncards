"""Download failures use synthetic data and responses; real networking is blocked."""
import base64
import csv
from concurrent.futures import Future
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import requests
import getall

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


class Response:
    def __init__(self, chunks=None, status=200):
        self.chunks = [PNG] if chunks is None else chunks
        self.status_code = status
        self.headers = {}
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError('synthetic HTTP failure', response=self)

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class ImmediateExecutor:
    """Complete submitted fixture work immediately to make request budgets deterministic."""
    def __init__(self, max_workers):
        pass

    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except Exception as error:
            future.set_exception(error)
        return future

    def shutdown(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.shutdown()


class DownloadRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='pokemon-download-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.images = self.root / 'images'
        self.images.mkdir()
        self.sets = self.root / 'sets.csv'
        self.cards = self.root / 'cards.csv'
        for name, value in [('DOWNLOADED_SETS_CSV', str(self.sets)),
                            ('DOWNLOADED_CARDS_CSV', str(self.cards)),
                            ('SETS_CACHE_FILE', str(self.root/'cache.json')),
                            ('MAX_WORKERS', 1), ('MAX_RETRIES', 0)]:
            self.replace(getall, name, value)
        network = patch('requests.sessions.Session.request', side_effect=AssertionError('Real network forbidden'))
        network.start()
        self.addCleanup(network.stop)
        output = redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        self.downloader = getall.PokemonCardDownloader()
        self.downloader.image_dir = str(self.images)

    def replace(self, target, name, value):
        change = patch.object(target, name, value)
        change.start()
        self.addCleanup(change.stop)

    def card(self, number=1, **overrides):
        row = dict.fromkeys(getall.CARDS_HEADERS, '')
        row.update(set_id='fixture', set_name='Fixture', card_number=str(number),
                   filename=f'{number}_hires.png', image_url='https://example.invalid/card.png',
                   status='pending', file_size='0', is_hires='True')
        row.update(overrides)
        return row

    def write_rows(self, path, headers, rows):
        with path.open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    def download(self, row, response):
        with patch.object(getall.requests, 'get', return_value=response) as request:
            success = self.downloader._download_card(('fixture', row['card_number']), row, str(self.images))
        return success, request

    def process(self, count, result):
        self.downloader.downloaded_cards_data = {('fixture', str(i)): self.card(i) for i in range(1, count+1)}
        with patch.object(getall, 'ThreadPoolExecutor', ImmediateExecutor), \
             patch.object(getall, 'as_completed', lambda futures: iter(futures), create=True), \
             patch.object(self.downloader, '_download_card', side_effect=result) as download, \
             patch.object(self.downloader, '_save_csv_data') as save, \
             patch.object(self.downloader, '_update_sets_completion'):
            self.downloader._process_downloads()
        return download, save

    def test_partial_checkpoint_is_rejected_without_returning_partial_records(self):
        self.write_rows(self.cards, getall.CARDS_HEADERS, [self.card()])
        with self.cards.open('a', encoding='utf-8') as f:
            f.write('broken,row\n')
        before = self.cards.read_bytes()
        with self.assertRaises(ValueError):
            getall.PokemonCardDownloader()
        self.assertEqual(self.cards.read_bytes(), before)

    def test_duplicate_checkpoint_keys_are_rejected(self):
        self.write_rows(self.cards, getall.CARDS_HEADERS, [self.card(), self.card()])
        with self.assertRaises(ValueError):
            getall.PokemonCardDownloader()

    def test_unknown_checkpoint_columns_survive_a_save(self):
        headers = getall.CARDS_HEADERS + ['collector_note']
        self.write_rows(self.cards, headers, [self.card(collector_note='Pokémon, keep this')])
        downloader = getall.PokemonCardDownloader()
        downloader._save_csv_data(str(self.cards), getall.CARDS_HEADERS, downloader.downloaded_cards_data)
        with self.cards.open(newline='', encoding='utf-8') as f:
            self.assertEqual(next(csv.DictReader(f))['collector_note'], 'Pokémon, keep this')

    def test_interrupted_stream_preserves_the_previous_file(self):
        row = self.card()
        folder = self.images/'Fixture'
        folder.mkdir()
        path = folder/row['filename']
        path.write_bytes(b'previous-file-retained')
        response = Response([PNG[:20], requests.ConnectionError('synthetic interruption')])
        success, _ = self.download(row, response)
        self.assertFalse(success)
        self.assertEqual(path.read_bytes(), b'previous-file-retained')
        self.assertTrue(response.closed)

    def test_empty_response_does_not_become_a_successful_image(self):
        row = self.card()
        success, _ = self.download(row, Response([]))
        self.assertFalse(success)
        self.assertFalse((self.images/'Fixture'/row['filename']).exists())

    def test_truncated_png_does_not_become_a_successful_image(self):
        row = self.card()
        success, _ = self.download(row, Response([PNG[:25]]))
        self.assertFalse(success)
        self.assertFalse((self.images/'Fixture'/row['filename']).exists())

    def test_complete_file_is_adopted_after_a_lost_checkpoint_without_download(self):
        row = self.card()
        folder = self.images/'Fixture'
        folder.mkdir()
        (folder/row['filename']).write_bytes(PNG)
        success, request = self.download(row, Response())
        self.assertTrue(success)
        request.assert_not_called()
        self.assertEqual(row['status'], 'success')
        self.assertEqual(int(row['file_size']), len(PNG))

    def test_path_traversal_is_rejected_before_requesting_or_writing(self):
        for fields in [{'set_name':'../outside'}, {'filename':'../../outside.png'},
                       {'filename':'..\\outside.png'}, {'set_name':str(self.root/'absolute')}]:
            with self.subTest(fields=fields):
                row = self.card(**fields)
                success, request = self.download(row, Response())
                self.assertFalse(success)
                request.assert_not_called()
        self.assertFalse((self.root/'outside.png').exists())

    def test_three_failed_cards_stop_new_requests(self):
        def fail(key, row, root):
            row['status'] = 'failed (Max Retries 0)'
            return False
        download, _ = self.process(100, fail)
        self.assertEqual(download.call_count, 3)
        self.assertTrue(self.downloader.stop_script)

    def test_checkpoint_is_not_rewritten_for_each_failure_after_fifty_successes(self):
        def finish(key, row, root):
            success = int(key[1]) <= 50
            row['status'] = 'success' if success else 'failed (Max Retries 0)'
            return success
        download, save = self.process(52, finish)
        self.assertEqual(download.call_count, 52)
        self.assertEqual(save.call_count, 2)

    def test_missing_previously_successful_image_is_requeued(self):
        row = self.card(status='success', file_size=str(len(PNG)))
        self.downloader.downloaded_cards_data = {('fixture','1'):row}
        with patch.object(self.downloader, '_download_card', return_value=True) as download, \
             patch.object(self.downloader, '_save_csv_data'), \
             patch.object(self.downloader, '_update_sets_completion'):
            self.downloader._process_downloads()
        download.assert_called_once()

    def test_rate_limit_stops_the_run_without_retries(self):
        self.replace(getall, 'MAX_RETRIES', 2)
        with patch.object(getall.time, 'sleep'):
            success, request = self.download(self.card(), Response(status=429))
        self.assertFalse(success)
        self.assertEqual(request.call_count, 1)
        self.assertTrue(self.downloader.stop_script)

    def test_successful_response_is_closed_and_checkpoint_matches_bytes(self):
        row = self.card()
        response = Response()
        success, _ = self.download(row, response)
        self.assertTrue(success)
        self.assertTrue(response.closed)
        self.assertEqual((self.images/'Fixture'/row['filename']).read_bytes(), PNG)
        self.assertEqual(int(row['file_size']), len(PNG))

    def test_invalid_checkpoint_headers_and_encoding_fail_closed(self):
        for content in [b'set_id,card_number\nfixture,1\n', b'set_id,set_id\na,b\n',
                        ','.join(getall.CARDS_HEADERS).encode() + b'\n\xff']:
            with self.subTest(content=content):
                self.cards.write_bytes(content)
                with self.assertRaises(ValueError):
                    getall.PokemonCardDownloader()
                self.assertEqual(self.cards.read_bytes(), content)

    def test_real_pool_stops_with_only_bounded_inflight_requests(self):
        self.replace(getall, 'MAX_WORKERS', 2)
        self.downloader.downloaded_cards_data = {('fixture',str(i)):self.card(i) for i in range(1,101)}
        with patch.object(getall.requests, 'get', side_effect=lambda *a, **k: Response(status=500)) as request:
            self.downloader._process_downloads()
        self.assertGreaterEqual(request.call_count, 3)
        self.assertLessEqual(request.call_count, 4)
        with self.cards.open(newline='', encoding='utf-8') as f:
            self.assertEqual(len(list(csv.DictReader(f))), 100)

    def test_checkpoint_excludes_unfinished_worker_mutations(self):
        self.replace(getall, 'MAX_WORKERS', 2)
        self.replace(getall, 'CHECKPOINT_BATCH_SIZE', 1)
        self.downloader.downloaded_cards_data = {('fixture',str(i)):self.card(i) for i in [1,2]}
        started = threading.Event()
        release = threading.Event()
        snapshots = []
        def worker(key, row, root):
            row['status'] = 'success'
            if key[1] == '2':
                started.set()
                if not release.wait(5):
                    raise RuntimeError('Fixture release did not arrive')
            return True
        def save(*args):
            self.assertTrue(started.wait(3))
            snapshots.append({key:dict(row) for key,row in self.downloader.downloaded_cards_data.items()})
            release.set()
        try:
            with patch.object(self.downloader, '_download_card', side_effect=worker), \
                 patch.object(self.downloader, '_save_csv_data', side_effect=save), \
                 patch.object(self.downloader, '_update_sets_completion'):
                self.downloader._process_downloads()
        finally:
            release.set()
        self.assertEqual(snapshots[0][('fixture','2')]['status'], 'pending')
        self.assertEqual(snapshots[-1][('fixture','2')]['status'], 'success')

    def test_interrupt_drains_completed_work_into_a_checkpoint(self):
        self.replace(getall, 'MAX_WORKERS', 2)
        self.downloader.downloaded_cards_data = {('fixture',str(i)):self.card(i) for i in [1,2,3]}
        def worker(key, row, root):
            row['status'] = 'success'
            return True
        with patch.object(getall, 'ThreadPoolExecutor', ImmediateExecutor), \
             patch.object(getall, 'wait', side_effect=KeyboardInterrupt), \
             patch.object(self.downloader, '_download_card', side_effect=worker):
            with self.assertRaises(KeyboardInterrupt):
                self.downloader._process_downloads()
        with self.cards.open(newline='', encoding='utf-8') as f:
            rows=list(csv.DictReader(f))
        self.assertEqual([r['status'] for r in rows], ['success','success','pending'])
        self.assertTrue(self.downloader.stop_script)

    def test_checkpoint_write_failure_preserves_disk_and_stops_new_work(self):
        self.replace(getall, 'CHECKPOINT_BATCH_SIZE', 1)
        self.write_rows(self.cards,getall.CARDS_HEADERS,[self.card()])
        before=self.cards.read_bytes()
        self.downloader.downloaded_cards_data = {('fixture',str(i)):self.card(i) for i in range(1,101)}
        def worker(key, row, root):
            row['status']='success'
            return True
        with patch.object(getall, 'ThreadPoolExecutor', ImmediateExecutor), \
             patch.object(self.downloader, '_download_card', side_effect=worker) as download, \
             patch.object(getall.os, 'replace', side_effect=OSError('Synthetic disk failure')) as replace:
            self.downloader._process_downloads()
        self.assertEqual(download.call_count,1)
        self.assertEqual(replace.call_count,1)
        self.assertEqual(self.cards.read_bytes(),before)
        self.assertTrue(self.downloader.stop_script)

    def test_colliding_destinations_stop_before_any_requests(self):
        self.downloader.downloaded_cards_data = {('fixture','1'):self.card(), ('other','1'):self.card(set_id='other')}
        with patch.object(getall.requests,'get') as request:
            with self.assertRaises(ValueError):
                self.downloader._process_downloads()
        request.assert_not_called()
        self.assertFalse(self.cards.exists())

    def test_declared_size_mismatch_preserves_previous_file(self):
        row=self.card()
        folder=self.images/'Fixture';folder.mkdir()
        path=folder/row['filename'];path.write_bytes(b'previous-file')
        response=Response();response.headers['Content-Length']=str(len(PNG)+1)
        success,_=self.download(row,response)
        self.assertFalse(success)
        self.assertEqual(path.read_bytes(),b'previous-file')
        self.assertTrue(response.closed)

    def test_not_found_is_not_retried(self):
        self.replace(getall,'MAX_RETRIES',2)
        success,request=self.download(self.card(),Response(status=404))
        self.assertFalse(success)
        self.assertEqual(request.call_count,1)

    def test_transient_failures_use_the_configured_attempt_limit(self):
        self.replace(getall,'MAX_RETRIES',2)
        with patch.object(getall.time,'sleep') as sleep:
            success,request=self.download(self.card(),Response(status=500))
        self.assertFalse(success)
        self.assertEqual(request.call_count,3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list],[2,4])

    def test_symlink_destination_is_rejected_without_changing_the_target(self):
        folder=self.images/'Fixture';folder.mkdir()
        target=self.root/'retained.png';target.write_bytes(PNG)
        try:
            (folder/'1_hires.png').symlink_to(target)
        except OSError:
            self.skipTest('Host does not grant symbolic-link creation')
        success,request=self.download(self.card(),Response())
        self.assertFalse(success)
        request.assert_not_called()
        self.assertEqual(target.read_bytes(),PNG)

    def test_missing_images_do_not_leave_a_set_marked_complete(self):
        self.downloader.downloaded_cards_data={('fixture','1'):self.card(status='success',file_size=str(len(PNG)))}
        self.downloader.downloaded_sets_data={'fixture':{'set_id':'fixture','set_name':'Fixture','printed_total':'1','cards_downloaded':'1','set_complete':'True'}}
        self.downloader._update_sets_completion()
        row=self.downloader.downloaded_sets_data['fixture']
        self.assertEqual(row['cards_downloaded'],0)
        self.assertEqual(row['set_complete'],'False')

    def test_a_single_failed_image_does_not_report_the_collection_complete(self):
        self.downloader.downloaded_cards_data={('fixture','1'):self.card()}
        output=io.StringIO()
        with patch.object(self.downloader,'_load_or_fetch_sets',return_value=True), \
             patch.object(self.downloader,'_populate_sets_csv'), \
             patch.object(self.downloader,'_populate_cards_csv'), \
             patch.object(getall.requests,'get',return_value=Response(status=404)), \
             redirect_stdout(output):
            self.downloader.run_full_download()
        self.assertNotIn('All required download and synchronization tasks are complete',output.getvalue())
        self.assertIn('Some images could not be downloaded',output.getvalue())

    def test_unknown_totals_do_not_overstate_set_completion(self):
        self.downloader.downloaded_cards_data={('fixture','1'):self.card(status='success')}
        self.downloader.downloaded_sets_data={'fixture':{'set_id':'fixture','set_name':'Fixture','printed_total':'unknown','cards_downloaded':'unknown','set_complete':'True'}}
        self.downloader._update_sets_completion()
        self.assertEqual(self.downloader.downloaded_sets_data['fixture']['set_complete'],'False')


if __name__ == '__main__':
    unittest.main()
