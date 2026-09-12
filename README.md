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

Checkpoint reads fail closed on malformed rows, missing or duplicate columns,
duplicate record keys and invalid encoding. Repair or restore the original file
before resuming; the utility does not replace it with partially read records.
Additional CSV columns are retained when progress is saved.

Downloads write to a temporary file and replace the destination only after a
successful response, a PNG boundary check and any available response-size check.
Failed or interrupted downloads retain an existing destination. Valid existing
images can repair a lost checkpoint without another request; missing or truncated
images previously marked successful are queued again. These checks do not decode
every pixel or verify the image against a provider checksum.

At most `MAX_WORKERS` requests are running or queued (10 by default). The utility
stops submitting new work after three failed cards; requests already running may
still finish. HTTP 429 stops the run immediately, so resume later after the
provider allows requests again. Other client errors are not retried. Transient
errors use at most `MAX_RETRIES + 1` attempts (one attempt by default).

Completed worker results are checkpointed in batches of 50 changed records and
once for the remaining changes on completion or interruption. Incomplete worker
updates are excluded from saved snapshots. A failed checkpoint replacement stops
new work and preserves the previous disk checkpoint. Existing collection files
and partial progress remain available to resume; a run with failed images does
not claim the collection is complete.

Set names and filenames must be single safe path components. Escaping paths,
symbolic links and conflicting destinations are rejected. Existing folder names
are kept; repair an invalid manifest explicitly instead of silently renaming its
downloads. Use one downloader process per collection directory: checkpoints do
not coordinate simultaneous independent processes.

## Tests

```sh
python -m unittest discover -s tests -v
```

The tests use synthetic files in temporary directories and block HTTP requests.
They cover metadata persistence, strict checkpoint loading, atomic image recovery,
bounded failures, rate limits, stable concurrent snapshots and interruptions.
CI runs all 38 cases on Linux and Windows; Windows may skip the symbolic-link case
when the host does not grant that capability. Tests use fake image responses and
do not start a provider download or read the repository's collection files.

This remains a local CLI with CSV/JSON checkpoints and image files. No Supabase
migration or hosted application deployment is established by these changes.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
