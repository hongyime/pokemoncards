# Agent State

Portfolio maintenance, 2026-09-11: PR #26 merged as
`f541bcd1e171c816000b09ccbaf42ccd39998a6d`. Existing-set metadata now persists
when a refresh discovers no new sets. The original source failed five of twelve
isolated tests; all twelve now pass locally and on Linux/Windows in PR and main
CI. Main run `34534253252` and all five source/security workflows passed.
The fix uses the existing atomic CSV writer and preserves download progress,
unlisted sets and unchanged checkpoints. Existing CSV/JSON collection files
remain byte-identical.

Remaining review: fail closed on partial checkpoint reads, make interrupted
image writes recoverable, bound download failures/queueing and validate paths.
These are separate from the completed metadata fix.

This repository is a local Python downloader with CSV/JSON checkpoints and image
files. It has no mapped Supabase/Vercel runtime. Tests must use synthetic temporary
files and block network access; do not run the downloader against an existing
collection as a maintenance test. Existing collection data must be preserved.
