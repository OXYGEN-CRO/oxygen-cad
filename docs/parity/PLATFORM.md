# Local platform contracts

Saved project commands remain the shared UI/agent transaction boundary. A durable journal contains the complete proposed source, history and undo state. Its fsynced publication commits the operation; reopening completes interrupted writes. Failed publication keeps the prior revision. This assumes a local filesystem with atomic rename and working fsync, not a network-filesystem database.

`Store.recover()` verifies snapshots against saved hashes, repairs either saved copy from its verified counterpart, and retains damaged bytes and prior audit metadata under `.o3d/recovery/`. If both copies of the latest revision are corrupt, it explicitly reports the fallback, quarantines damaged snapshots, and leaves continued editing possible. Future schema versions are rejected without rewriting them.

Portable `.oxygen.zip` archives contain the saved source, all retained revisions, every local immutable asset, undo state and saved review metadata. Available build/study evidence is included by default. The manifest names and hashes every member. Credentials, jobs, drafts and recursively generated exports are excluded. Limits are 512 MiB uncompressed and 100,000 files. Restore validates the complete archive and dependencies before atomically adopting a new directory; it never overwrites an existing project. A manufacturing release includes this portable project and can be restored directly. Export refuses a concurrent source change rather than mixing revisions.

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
