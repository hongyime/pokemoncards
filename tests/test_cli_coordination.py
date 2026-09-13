"""Real independent processes use only owned temporary fixture directories."""
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import getall
from collection_lock import collection_lock, LOCK_NAME

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / 'tests' / 'cli_probe.py'


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='pokemon-process-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {**os.environ, 'PYTHONPATH': str(ROOT), 'PYTHONIOENCODING': 'utf-8'}

    def start(self, mode, marker, root=None):
        return subprocess.Popen([sys.executable, str(PROBE), mode, marker], cwd=root or self.root,
                                env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

    def ready(self, process):
        result = queue.Queue()

        def read():
            for line in process.stdout:
                if line.strip() == 'FIXTURE_READY':
                    result.put(True)
                    return
            result.put(False)
        threading.Thread(target=read, daemon=True).start()
        self.assertTrue(result.get(timeout=10), 'Owned child must reach its menu')

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)

    def test_second_cli_stops_before_reading_stale_checkpoints(self):
        first = self.start('hold', 'first.marker')
        try:
            self.ready(first)
            second = self.start('exit', 'second.marker')
            try:
                stdout, stderr = second.communicate(timeout=10)
                self.assertEqual(second.returncode, 2, stdout + stderr)
                self.assertFalse((self.root / 'second.marker').exists())
                self.assertIn('already in use', stderr)
            finally:
                self.stop(second)
            first.communicate('4\n', timeout=10)
            self.assertEqual(first.returncode, 0)
        finally:
            self.stop(first)

    def test_a_crashed_owner_does_not_leave_a_stale_process_lock(self):
        first = self.start('hold', 'first.marker')
        try:
            self.ready(first)
            self.stop(first)
            second = self.start('exit', 'second.marker')
            try:
                stdout, stderr = second.communicate(timeout=10)
                self.assertEqual(second.returncode, 0, stdout + stderr)
                self.assertTrue((self.root / 'second.marker').exists())
            finally:
                self.stop(second)
        finally:
            self.stop(first)

    def test_different_collection_directories_are_independent(self):
        other = self.root / 'other'; other.mkdir()
        first = self.start('hold', 'first.marker')
        try:
            self.ready(first)
            second = self.start('exit', 'other.marker', other)
            try:
                stdout, stderr = second.communicate(timeout=10)
                self.assertEqual(second.returncode, 0, stdout + stderr)
                self.assertTrue((other / 'other.marker').exists())
            finally:
                self.stop(second)
        finally:
            self.stop(first)

    def test_normal_exit_releases_lock_without_removing_its_file(self):
        with collection_lock(self.root):
            original_inode = (self.root / LOCK_NAME).stat().st_ino
        self.assertTrue((self.root / LOCK_NAME).exists())
        with collection_lock(self.root):
            self.assertEqual((self.root / LOCK_NAME).stat().st_ino, original_inode)

    def test_lock_failure_prevents_downloader_initialization(self):
        with patch.object(getall, 'collection_lock', side_effect=PermissionError('Synthetic lock failure')), \
             patch.object(getall, 'PokemonCardDownloader') as downloader:
            self.assertEqual(getall.main(), 2)
        downloader.assert_not_called()

    def test_symbolic_lock_is_rejected_without_touching_its_target(self):
        target = self.root / 'kept.txt'; target.write_text('keep', encoding='utf-8')
        try:
            (self.root / LOCK_NAME).symlink_to(target)
        except OSError:
            self.skipTest('Host does not permit symbolic links')
        with self.assertRaises(OSError):
            with collection_lock(self.root):
                self.fail('Symbolic lock must be rejected')
        self.assertEqual(target.read_text(), 'keep')

    @unittest.skipUnless(os.name == 'nt', 'Windows batch launcher')
    def test_windows_launcher_keeps_all_input_and_returns_python_exit_status(self):
        shutil.copyfile(ROOT / 'run_scraper.bat', self.root / 'run_scraper.bat')
        (self.root / 'getall.py').write_text(
            "import sys\nprint('INPUT:' + '|'.join(input() for _ in range(3)), flush=True)\nsys.exit(41)\n", encoding='utf-8')
        result = subprocess.run(['cmd.exe', '/d', '/c', 'run_scraper.bat'], cwd=self.root, env=self.env,
                                input='1\nfixture directory\n4\n', capture_output=True, text=True,
                                encoding='utf-8', timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertIn('INPUT:1|fixture directory|4', result.stdout)
        self.assertEqual(result.returncode, 41)

    @unittest.skipUnless(os.name == 'nt', 'Windows batch setup')
    def test_windows_setup_uses_matching_interpreter_and_pinned_requirements(self):
        shutil.copyfile(ROOT / 'set_up.bat', self.root / 'set_up.bat')
        # A fixture executable wrapper records arguments; it installs nothing.
        wrapper = self.root / 'python.cmd'
        wrapper.write_text('@echo off\necho ARGS:%*>> "%POKEMON_FIXTURE_ARGUMENTS%"\nexit /b 0\n', encoding='utf-8')
        output = self.root / 'arguments.txt'
        env = {**self.env, 'PATH': str(self.root) + os.pathsep + self.env['PATH'], 'POKEMON_FIXTURE_ARGUMENTS': str(output)}
        result = subprocess.run(['cmd.exe', '/d', '/c', 'set_up.bat'], cwd=self.root, env=env,
                                input='\n', capture_output=True, text=True, encoding='utf-8',
                                timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(output.read_text().splitlines(), [
            'ARGS:-m pip --version', f'ARGS:-m pip install -r "{self.root / "requirements.txt"}"'])


if __name__ == '__main__':
    unittest.main()
