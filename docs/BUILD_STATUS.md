# Build and release status

The 0.8.0 implementation candidate adds all twelve mission-quality and managed-system capabilities described in [QUALITY_AND_SYSTEMS.md](QUALITY_AND_SYSTEMS.md). Local checks have passed 225 Python tests (one Windows symlink-permission test skipped), the existing 40 headless UI flows plus the new graph-diagnostics flow, and three native request-boundary checks. Cross-platform package and disposable Docker acceptance are being verified before the public channel advances. The verified 0.7.0 release below remains the current published baseline until that evidence is recorded.

12 October 2026 · version 0.7.0 · MIT

[Swarm Workbench 0.7.0 is published](https://github.com/AngadBasandrai/swarmfree/releases/tag/v0.7.0) for Windows x64, macOS Apple silicon/Intel, and Linux x64. Its immutable source is `150c11ca0efd7b688330ab99407e4db8740d472a`.

The desktop architecture workshop adds all twelve controls: live recursive task trees, dynamic fanout, a drag/drop node palette, typed field connections, extracted reusable blocks, saved revision diffs/restores, inherited branch allowances, repetition detection, durable breakpoints, paused branch editing, personal agent blueprints and project-scoped collaborative drafts. Parameter forms and named interfaces support reusable blocks. See [ARCHITECTURES.md](ARCHITECTURES.md) for controls and hard bounds.

Verified acceptance:

- [Source CI](https://github.com/AngadBasandrai/swarmfree/actions/runs/38268092363): 202 Python tests and 37 headless Chromium flows pass on each of Windows, macOS and Linux. Workshop tests exercise typed projection, child creation, whole-fanout rejection, checkpoint recovery, independent approvals, retained responses, child replacement, permission restrictions, saved revisions, scope checks and conflict handling. UI flows exercise palette drop, extraction, history restoration, actual fanout, branch editing, two-editor merging and conflict recovery.
- [Desktop builds and publication](https://github.com/AngadBasandrai/swarmfree/actions/runs/38268092348): all four platform builds and publication pass. Native request boundaries, frozen-engine execution and package checks run in CI. Windows installation acceptance runs on the disposable runner.
- Independent archive inspection verifies Windows installer/portable/worker engine equality, both Mac architectures, Linux AppImage/Deb engine equality, executable permissions, notices and included architecture documentation. All 21 published desktop binary/signature hashes match the checked CI artifacts.
- Four updater signatures and all 26 unique checksum entries verify. All 23 registry packages verify against the existing pinned publisher identity and SHA-256 hashes. The HTTPS update feed matches the published release.
- [Native companion builds](https://github.com/AngadBasandrai/swarmfree/actions/runs/38268092426): Android arm64 development APK and unsigned iOS simulator compilation pass. APK signature and native payload checks pass; simulator version, architecture, permissions and documents pass. Published provenance binds both assets to the release tag and build run.
- Local acceptance passes the backend suite, focused workshop checks, 37 UI flows, three native request-boundary checks and isolated frozen Windows engine acceptance, including durable gates, shared drafts and real fanout.

The live download page displays v0.7.0 with seven verified download links. Narrow layout and image preview checks pass. The public domain serves distribution metadata; model inference, provider credentials, shared design drafts and mission state stay on the chosen coordinators.

Validation scope:

Automated model responses come from owned fixtures. Live provider accounts, real model weights, GPU workloads and physical linked devices remain separate acceptance work. Repetition detection compares matching task/data/team semantics; it does not measure semantic similarity. Active requests reach a safe boundary before branch holds take effect. Depth, graph expansion, root limits and inherited branch allowances bound execution. Presence is transient; draft revisions and edit history are persistent. Shared sessions support at most 1,000 edit revisions before another session is needed.

Model weights and optional inference, search, transcription, browser, container and CUDA runtimes are separate installations. Android uses development signing; physical iOS distribution requires Apple signing and provisioning. Updater signatures are separate from Windows Authenticode and macOS notarization.

No desktop GUI, installer, native dialog or simulator runs on the user's machine. Headless test browsers and owned background engines run in isolation. The older installed application remains untouched. See [RELEASE_OPERATIONS.md](RELEASE_OPERATIONS.md), [FEATURES_0_6.md](FEATURES_0_6.md) and [COMPANION.md](COMPANION.md).
