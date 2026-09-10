# pokemoncards

Command-line Pokémon card image downloader. `getall.py` reads set metadata from
the Pokémon TCG API or a local JSON cache, tracks progress in CSV files and
downloads images into a directory you choose.

## Setup

Use Python 3.11 or newer. From the repository directory:

```sh
python -m venv .venv
```

Activate it with `.venv\Scripts\Activate.ps1` in PowerShell or
`source .venv/bin/activate` in Bash, then install the HTTP dependency:

```sh
python -m pip install -r requirements.txt
```

## Usage

```sh
python getall.py
```

The menu can start/resume downloads, refresh the set list before resuming,
show statistics, or exit. Download options contact the image provider and write
files. Statistics also load set metadata and may contact the API if the cache
is unavailable.

`sets_cache.json`, `downloaded_sets.csv` and `downloaded_cards.csv` are read and
written relative to the current directory. Images use the directory selected
in the prompt. Keep existing files when resuming a collection.

Set names, printed totals, upstream update dates and local check times are saved
even when a refresh adds no new sets. This metadata step preserves download
counts, completion flags and sets absent from the latest response. A completely
unchanged checkpoint is not rewritten. CSV saves use temporary-file replacement;
a failed replacement preserves the previous checkpoint and stops the run.

## Tests

```sh
python -m unittest discover -s tests -v
```

The tests use synthetic files in temporary directories and block HTTP requests.
They cover metadata persistence after restarting, unchanged checkpoints, missing
files, failed replacement and preservation of existing progress. CI runs them
on Linux and Windows. Tests do not start a download or read the repository's
collection files.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
