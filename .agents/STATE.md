# Agent State

Portfolio maintenance, 2026-09-12: download recovery repair is ready for hosted
review. Baseline fixtures reproduced 13 unsafe behaviors. The revised suite has
38 cases: 37 pass locally on Windows and one symbolic-link case is skipped because
the host does not grant link creation. Hosted Linux must cover that case.

Changes: strict complete CSV reads preserve unknown columns and fail closed on
invalid checkpoints; image writes use temporary-file replacement and retain prior
files on failure; PNG boundaries and available sizes are checked; missing images
are requeued and existing images can repair lost progress. Paths and colliding
destinations are checked before writes. Outstanding tasks are bounded by worker
count, new submissions stop after three failed cards, and HTTP 429 stops the run.
Worker copies are merged only after completion. Checkpoints save changed records
in batches and drain completed work on interruption without clobbering failed
checkpoints. Runs with unfinished images no longer report complete success.

Next: all hosted source/security checks must pass before normal PR merge; verify
exact main source/checks, preserve original collection hashes during local sync,
and update the shared Markdown/PostPlan report. Existing workflows and pinned
dependencies remain unchanged; legacy bot merge workflows stay paused.

Previous metadata fix PR #26 remains covered by twelve restart/preservation tests.
The new tests use synthetic temporary files, fake responses and blocked network.
No existing collection file is read by tests and no provider download is invoked.

This is a local Python CLI with CSV/JSON checkpoints and image files. There is no
mapped Supabase or Vercel runtime. PNG checks do not decode pixels or prove provider
identity, and independent downloader processes do not share a transaction/lock.
Cache atomicity, launcher behavior and provider catalog completeness still need
review. The wider portfolio and Supabase capacity/migration work remain open.
