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

2026-09-12 maintenance: the LFS guard now checks out one commit because it scans the current index. Git scan errors fail the job instead of appearing to be a successful empty scan. The shared source change is verified in [sourcerepo PR #51](https://github.com/hongyime/sourcerepo/pull/51), with all ten Linux fixtures passing. Existing pointer rejection, opt-out behavior and action references are preserved. This workflow-only change leaves application code and data unchanged. Release requires passing hosted checks, followed by verification of the merged main workflow.

2026-09-13 cache and coordination continuation: metadata rows and download recovery are already released. Reproduce direct JSON-cache truncation, overlapping CLI processes and Windows launcher input failures using synthetic files and blocked network. Preserve existing records and serialize cooperating collection access. Five tasks are recorded in .agents/handoffs/pokemon-cache-20260913.json; the full portfolio scope remains active.

2026-09-13 cache/CLI fixes pass locally: temporary JSON writes plus fsync/replacement preserve the last good cache on serialization, interruption, sync or sharing failure; malformed cached/provider metadata is rejected before replacement. One OS lock is held before CLI checkpoint reads through exit and releases on crashes without deleting its lock file. The Windows launcher retains interactive stdin and Python exit status; setup uses the pinned requirements with the same interpreter. All 57 local cases run: 55 pass and two symbolic-link cases are host-capability skips; Ruff fatal checks pass and Bandit reports zero findings. Hosted Linux/Windows and source release verification remain pending. Original tracked collection files are unchanged and no provider downloads occurred.
