# Build and release status

12 October 2026 · version 0.6.5 · MIT

[Swarm Workbench 0.6.5 is published](https://github.com/AngadBasandrai/swarmfree/releases/tag/v0.6.5) for Windows x64, macOS Apple silicon/Intel, and Linux x64. The immutable release source is `9d69f206e0a11dafe6b1ba97f8cefd7002cae789`.

This release adds 22 architecture presets, a visual editor with search/categories, node duplication, undo/redo, automatic layout and connection controls, and bounded recursive decomposition, critique/repair and fork/join. Recursive missions use real model base cases, frozen revisions, shared budgets, permission intersections, checkpoint reuse and child-team navigation. See [ARCHITECTURES.md](ARCHITECTURES.md). The removed demonstration mode remains disabled.

Verified acceptance:

- [Source CI](https://github.com/AngadBasandrai/swarmfree/actions/runs/38250978927): 179 Python tests and 31 headless Chromium flows pass on each of Windows, macOS and Linux. Every preset executes against an owned provider fixture; recursion tests cover depth, early stopping, shared budgets, permission intersection and restart recovery. UI checks cover editing, mobile width and child navigation.
- [Desktop artifacts](https://github.com/AngadBasandrai/swarmfree/actions/runs/38250979084): all four platform builds and publication pass. Source tests, native request boundaries, frozen-engine acceptance and package checks run in CI; Windows installation acceptance runs only on the disposable runner.
- Independent archive inspection verifies Windows installer/portable/worker engine equality, both Mac architectures, Linux AppImage/Deb engine equality, executable permissions, notices and included architecture documentation. All 21 public desktop binary/signature asset hashes match the checked CI artifacts.
- Four updater platform signatures and all 26 unique checksum entries verify. All 23 registry packages verify against the existing pinned publisher key and SHA-256 hashes. Publisher identities are unchanged.
- [Native companion builds](https://github.com/AngadBasandrai/swarmfree/actions/runs/38250979157): Android arm64 development APK and unsigned iOS simulator compilation pass. APK signature, ZIP integrity and native architecture checks pass. Simulator version, Mach-O architecture, permissions and bundled documents pass. Companion provenance binds both assets to the release tag and build run.
- Local acceptance also passes three native request-boundary checks and isolated frozen Windows engine acceptance, including recursive child execution. No desktop GUI, installer, native dialog or simulator runs on the user's machine; host input and focus are not automated.

[Pages deployment](https://github.com/AngadBasandrai/swarmfree/actions/runs/38255202318) succeeds. The live download page displays v0.6.5 with seven verified platform download links, and its narrow layout and image preview pass headless checks. The HTTPS update feed matches the published release manifest; all 23 packages served by the live registry verify. Hosting contains distribution metadata; inference, provider credentials and mission state stay on owners' devices.

Validation scope:

Automated model responses come from owned fixtures. Live provider credentials, real model weights, GPU training, physical linked devices and Apple silicon sharding remain separate acceptance work. Stopping markers are model-generated heuristics; depth, expansion and mission limits provide the hard bounds. Hardware budgets are admission estimates, and declared runtime revisions cannot attest loaded weights.

Model weights and optional inference, search, transcription, browser, container and CUDA runtimes are separate installations. Android uses development signing; physical iOS distribution needs Apple signing and provisioning. These artifacts are not Play/App Store releases. Updater signatures are separate from Windows Authenticode and macOS notarization.

The older installed application remains untouched. See [FEATURES_0_6.md](FEATURES_0_6.md), [COMPANION.md](COMPANION.md) and [RELEASE_OPERATIONS.md](RELEASE_OPERATIONS.md) for supported scope.
