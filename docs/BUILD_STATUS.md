# Build and release status

12 October 2026 · version 0.7.0 candidate · MIT

This candidate adds the architecture workshop: a live recursive task tree, dynamic fanout, a drag/drop node palette, typed field connections, extracted reusable blocks, architecture parameters, saved version diffs/restores, inherited branch allowances, repetition detection, durable breakpoints, paused branch editing, personal agent blueprints and project-scoped collaborative drafts. See [ARCHITECTURES.md](ARCHITECTURES.md) for controls, hard bounds and supported behavior.

Local acceptance passes the 201-test backend regression suite, all 23 focused workshop tests (including an additional independent-gate approval check), 37 headless Chromium flows and three native request-boundary checks. UI checks include actual variable child missions, branch editing, block extraction, version restoration, two-editor merging and conflict recovery. The release candidate is versioned consistently across the engine, desktop and native companion. All 23 registry packages verify against the existing publisher identity.

The isolated frozen-engine check covers real provider-fixture inference, nested execution, durable gates, shared drafts and fanout. Desktop and companion distribution builds, immutable release publication, updater signatures, package checksums and live hosting are verified separately before the public release is recorded here. The previously verified downloadable release is [0.6.5](https://github.com/AngadBasandrai/swarmfree/releases/tag/v0.6.5).

Automated models are owned fixtures. Live provider accounts, real model weights, GPU workloads and physical linked devices remain separate acceptance work. Presence is transient; collaborative drafts and edit history are persistent. Repetition detection compares matching inputs/team semantics; it does not measure semantic similarity. Active requests reach a safe boundary before branch holds take effect. Depth, graph expansion, root limits and inherited allowances bound admitted execution.

Model weights and optional inference, search, transcription, browser, container and CUDA runtimes are separate installations. Android uses development signing; physical iOS distribution requires Apple signing and provisioning. Updater signatures are separate from Windows Authenticode and macOS notarization.

No desktop GUI, installer, native dialog or simulator runs on the user's machine. Headless test browsers and owned background engines run in isolation. The installed older application remains untouched. See [RELEASE_OPERATIONS.md](RELEASE_OPERATIONS.md), [FEATURES_0_6.md](FEATURES_0_6.md) and [COMPANION.md](COMPANION.md).
