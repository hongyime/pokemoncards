"""Child-process CLI fixture. Its collection and marker paths are synthetic."""
from pathlib import Path
import sys
from unittest.mock import patch

import getall


class FixtureDownloader:
    stop_script = False

    def __init__(self):
        Path(sys.argv[2]).write_text('constructed', encoding='utf-8')


def fixture_input(prompt):
    if sys.argv[1] == 'hold':
        print('FIXTURE_READY', flush=True)
        value = sys.stdin.readline()
        if not value:
            raise EOFError
        return value.strip()
    return '4'


with patch.object(getall, 'PokemonCardDownloader', FixtureDownloader), \
     patch('builtins.input', side_effect=fixture_input), \
     patch('requests.sessions.Session.request', side_effect=AssertionError('Provider access forbidden')):
    raise SystemExit(getall.main())
