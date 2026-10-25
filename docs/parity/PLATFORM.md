# Local platform contracts

Saved project commands remain the shared UI/agent transaction boundary. A durable journal contains the complete proposed source, history and undo state. Its fsynced publication commits the operation; reopening completes interrupted writes. Failed publication keeps the prior revision. This assumes a local filesystem with atomic rename and working fsync, not a network-filesystem database.

`Store.recover()` verifies snapshots against saved hashes, repairs either saved copy from its verified counterpart, and retains damaged bytes and prior audit metadata under `.o3d/recovery/`. If both copies of the latest revision are corrupt, it explicitly reports the fallback, quarantines damaged snapshots, and leaves continued editing possible. Future schema versions are rejected without rewriting them.

Job detail is authoritative; the compact polling summary is a derived cache. If
only summary publication fails, polling uses a compact memory copy and detail
downloads retain the durably saved result. Missing or malformed summaries are
rebuilt from detail once, rather than reading dense geometry on every poll.

If a terminal job record cannot be saved, the running application reports a
failed, memory-only status with no passing result; a cancelled worker remains
cancelled. Cancellation bookkeeping is released even if the final write fails.
Existing artifacts are not deleted, and a domain revision already committed by
the guarded finalizer is not rolled back. Inspect the current project before
retrying: a failed status publication does not prove that no edit was applied.

Memory-only status cannot survive an application restart. On reopening, durable
queued/running records become interrupted failures without rerunning workers,
even if job status storage remains unavailable. When storage works again, a
normal reopening can persist that failure. This is not a claim that a full disk
can retain new durable evidence or survive power loss without a saved record.
Submissions that cannot store their record/request fail with HTTP 507 for full
disk/quota exhaustion, or 503 for other storage errors, and include a job ID for
inspection. No automatic operation retry is performed.

Portable `.oxygen.zip` archives contain the saved source, all retained revisions, every local immutable asset, undo state and saved review metadata. Available build, study, assembly, drawing, manufacturing, electronics, visualization, interop and domain simulation evidence is included by default. Saved artifact pointers are dependencies and remain included even when bulk cached results are omitted. Every current and historical asset registry is checked against its original declared hash; derived-asset ancestry and native linked-source registration must remain complete. The manifest names and hashes every member. Credentials, jobs, drafts and recursively generated exports are excluded. Limits are 512 MiB uncompressed and 100,000 files. Restore validates the complete archive and dependencies before atomically adopting a new directory; it never overwrites an existing project. A manufacturing release includes this portable project and can be restored directly. Export refuses a concurrent source change rather than mixing revisions.

Project directory identifiers contain 1–64 ASCII letters, numbers, hyphens or underscores. Catalog and archive paths reject traversal and symbolic links. Duplicate makes a complete independent project with a new project identity. Archive preserves files and is reversible. The parent application opens each `/p/{directory}/` in an independent Store/Jobs scope; no mutable global project-directory switch is required.

Optional project credentials protect HTTP API operations with random tokens, hashes at rest, revocation, role checks and attributable edits. A browser session uses an HttpOnly, SameSite-strict, per-project cookie. The first trusted-local owner may enable credentials; subsequent identities require an authorized manager, and only an administrator can create/revoke an administrator. The last administrator cannot be revoked. Agents receive an authenticated `kind=agent`; changing the submitted actor label cannot bypass user locks. Mutations re-read current account state under the Store lock. The parent job runner must preserve that identity through worker and finalization boundaries.

| Role | HTTP capabilities |
|---|---|
| Viewer | Read project state; downloadable artifacts are denied |
| Reader | Read, export, comment |
| Editor | Reader capabilities plus edit |
| Manager | Editor capabilities plus approve review, issue/revoke non-administrator credentials |
| Administrator | All project HTTP capabilities |

These credentials are not a sandbox against the local filesystem owner or a trusted CLI process, and no hosted identity, organization/folder service, cloud synchronization or public share-link system is provided. A browser that renders geometry inherently receives data; download restrictions must not be described as preventing copying rendered models. Cross-project management remains a trusted-local operation; each authenticated project opens with its own credentials.

Review decisions, comments and named checkpoints bind to an immutable revision and full document hash. Every later source change makes an earlier approval stale. Offline source drafts never silently replace newer work: recursive three-way reconciliation reports conflicting paths, and an explicit revision-checked apply remains required. Structural validation is not proof that merged geometry rebuilds. The panel persists its source draft locally, offers project-directory draft recovery, and registers a browser unsaved-close warning; native WKWebView behavior remains a physical-desktop acceptance gate.

Desktop startup uses versioned, content-addressed runtime images and keeps persistent projects outside application resources. Docker port responses must resolve to a single assigned IPv4 loopback address. Failed startup/retry cleans up its recorded container; subsequent startup can reclaim a dead host process's container only after inspecting its matching ownership label. Docker commands have bounded timeouts and bounded diagnostic tails. The resource stager derives its source closure from Docker COPY inputs and tracked public files; untracked data, projects, caches, credentials and symlinks cannot enter that release path. An image-mode bundle includes the Docker image archive for offline first setup; source mode can require network access and build time.

Linux policy tests and type checking do not establish Apple Silicon installation, Docker failure recovery, WKWebView behavior, signing, notarization or delivered update rollback. Those remaining gates are recorded alongside all 18 platform rows in `platform.json`.

The platform browser regression runs against the integrated application in a disposable catalog, with an actual imported STEP dependency. Start `PYTHONPATH=<checkout>/src O3D_ENGINE_BINARY=<engine> .venv/bin/python tests/platform_browser_server.py` on port 9371, then run `O3D_BROWSER_URL=http://127.0.0.1:9371 O3D_PLATFORM_BROWSER_FIXTURE=1 npx playwright test tests/platform-lifecycle.spec.ts` from `frontend/`. The fixture never uses port 8787 and removes its child projects at shutdown. The measured flow covers create/rename/duplicate/archive/reopen, browser draft recovery after reload, revision review becoming stale, downloading/restoring the complete archive, and independent project-window editing. It does not certify other workspaces or physical Mac behavior.
