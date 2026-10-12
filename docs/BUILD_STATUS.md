# Build and release status

13 October 2026 · version 0.8.1 · MIT

[Swarm Workbench 0.8.1 is published](https://github.com/AngadBasandrai/swarmfree/releases/tag/v0.8.1) for Windows x64, macOS Apple silicon/Intel, and Linux x64. Its immutable source is `0cbd0e37c7394b54bba6ce4c937e36aa212dac0b`.

The downloadable desktop app adds all twelve mission-quality and managed-system capabilities: mission briefs, architecture linting, failure/timeout branches, reviewed task reuse, provenance-aware artifact assembly, repeated agent/edge ablations, promotion gates, checkpoint comparisons, encrypted workspace sync, verified runtime installation/rollback, private vLLM cluster controls, and isolated local extensions. See [QUALITY_AND_SYSTEMS.md](QUALITY_AND_SYSTEMS.md) and [FEATURES_0_8.md](FEATURES_0_8.md).

Verified acceptance:

- [Source CI](https://github.com/AngadBasandrai/swarmfree/actions/runs/38293500422): 226 Python tests and 41 headless Chromium flows pass on each of Windows, macOS and Linux. Tests cover error recovery, hard budget limits, acceptance failures, real checkpoint forks, immutable child revisions, shared experiment budgets, child tool restrictions/accounting, duplicate approvals, assembly disagreements, encrypted sync conflicts/replay, archive traversal and runtime tampering. The editor test deliberately delays a lint response while editing the draft and verifies every change persists.
- [Desktop builds and publication](https://github.com/AngadBasandrai/swarmfree/actions/runs/38293500327): all four platform builds and publication pass. Native request boundaries, frozen-engine execution and package checks run in CI. Windows installation acceptance runs on a disposable runner.
- Independent archive inspection verifies Windows installer/portable/worker engine equality, both Mac architectures, Linux AppImage/Deb engine equality, executable permissions, dependency notices and bundled architecture/quality/system guides. All 21 published desktop binary/signature hashes match the checked CI artifacts.
- Four updater signatures and all 26 unique checksum entries verify. All 23 registry packages verify against the existing pinned publisher identity, match their SHA-256 hashes, and declare version 0.8.1. The HTTPS feed matches the published release.
- [Actual Docker/MCP acceptance](https://github.com/AngadBasandrai/swarmfree/actions/runs/38293500302): a disposable Linux runner verifies selected-file access, read-only workspace enforcement, denied networking, exact secret redaction and owned-container cleanup through the real stdio protocol.
- [Native companion builds](https://github.com/AngadBasandrai/swarmfree/actions/runs/38293500296): Android arm64 development APK and unsigned iOS simulator compilation pass. APK signature/native payload checks and simulator version, architecture, permissions and document checks pass. Published provenance binds both assets to the immutable tag and build run.

Version 0.8.0 remains an immutable unsuccessful validation candidate and was not published to the update channel. Version 0.8.1 fixes asynchronous draft-edit loss, mixed text encoding, a Windows test deadline, reserved checkpoint identifiers, and child-team evaluation accounting.

Automated model responses use owned fixtures. Live provider accounts, real model weights, GPU workloads, physical linked devices, and actual multi-host vLLM performance/model compatibility remain separate hardware acceptance work. Runtime installation tests verify extraction and file integrity; driver/runtime/model compatibility requires a successful load on the target device. Duplicate similarity is lexical and requires owner review. Acceptance checks establish only their measured properties.

Model weights and optional inference, browser, container, search, transcription, training and CUDA runtimes are separate installations. Private vLLM sharding requires trusted approved Linux GPU workers; public WAN sharding is excluded. Android uses development signing; physical iOS distribution requires Apple provisioning. Updater signatures are separate from Windows Authenticode and macOS notarization.

No desktop GUI, installer, native dialog or simulator runs on the user's machine. Headless browsers and owned background engines run in isolated fixtures. The older installed app remains untouched. See [RELEASE_OPERATIONS.md](RELEASE_OPERATIONS.md) for release checks.
