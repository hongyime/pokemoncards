# Agent State

Portfolio maintenance, 2026-09-11: fixing set metadata that changed in memory
but was not saved when a refresh discovered no new sets. The original source
failed five of twelve isolated checkpoint tests. The fix detects changes to
existing metadata and uses the existing atomic CSV writer while preserving
download progress and unlisted sets. All twelve isolated tests now pass locally;
Linux/Windows CI and publication to the default branch are pending.

This repository is a local Python downloader with CSV/JSON checkpoints and image
files. It has no mapped Supabase/Vercel runtime. Tests must use synthetic temporary
files and block network access; do not run the downloader against an existing
collection as a maintenance test. Existing collection data must be preserved.
