# Swarm Workbench build status

12 October 2026 · version 0.6.5 candidate · MIT

The 0.6.5 candidate adds 22 architecture presets, the new visual editor and bounded recursive child teams. Local acceptance passes 179 Python tests, 31 headless Chromium flows, three native request-boundary tests, frontend compilation, and isolated frozen Windows engine acceptance including recursive calls. Coverage includes every preset with owned model fixtures, depth/stop/budget controls, restart reuse, preflight estimates and editor/navigation workflows. Publication checks are in progress.

Last published release: [Swarm Workbench 0.6.4](https://github.com/AngadBasandrai/swarmfree/releases/tag/v0.6.4) for Windows x64, macOS Apple silicon/Intel, and Linux x64. The release source is the immutable commit `8c4caf12cd8ed0a6cccc12bbc2960b40e9a47334`.

The offline demonstration backend and controls have been removed from missions, automations, campaigns, comparisons, evaluations, and onboarding. Missions require connected models; missing capacity is reported or queued according to the owner's selection. Old demonstration records remain inspectable and exportable, with resume, branch, and chat disabled. Tests provide their own model fixtures outside the normal application entry point.

Verified release evidence:

- [Source CI](https://github.com/AngadBasandrai/swarmfree/actions/runs/38242477032): 141 Python tests and 27 headless Chromium flows pass on each of Windows, macOS, and Linux. Coverage includes rejected demonstration requests, no-model behavior, legacy-record retention, graph handoffs, restart recovery, and the existing command-center features.
- [Four-platform packages and publication](https://github.com/AngadBasandrai/swarmfree/actions/runs/38242477025): all four targets pass frozen-engine acceptance and updater-signature checks. The actual frozen executable rejects demonstration-mode requests. Model turns use owned loopback protocol fixtures. Disposable Windows CI passes silent installation, installed engine verification, and uninstall.
- Independent archive inspection verifies Windows installer/portable/worker engine equality, both Mac architectures, Linux AppImage/Deb engine equality, executable permissions, and bundled notices. All 21 public desktop binary/signature asset hashes match the checked CI artifacts.
- Four updater platform signatures and all 26 unique checksum entries verify. The HTTPS domain update feed matches the published GitHub release manifest. All 11 registry packages verify against the pinned publisher key and their SHA-256 hashes; their instructions now require connected models.
- [Native companion builds](https://github.com/AngadBasandrai/swarmfree/actions/runs/38242477117): Android arm64 development APK and unsigned iOS simulator compilation pass. APK signature, ZIP integrity, and native architecture checks pass. Simulator version, Mach-O architecture, executable permissions, and bundled documents pass. Companion provenance binds both phone files to the release tag's source commit and this build run.
- [Pages deployment](https://github.com/AngadBasandrai/swarmfree/actions/runs/38245176101) succeeds. The download page, registry, and update feed are served at swarms.grafitelab.in. Hosting contains distribution metadata and a download page; inference and provider credentials stay on owners' devices.
- Local verification also passes three native request-boundary tests and isolated frozen Windows engine acceptance. The development test-provider helper is absent from the frozen engine's module inventory.

Validation scope:

Protocol fixtures supply automated test responses. Live provider credentials, real model weights, GPU training, physical linked devices, and Apple silicon sharding remain separate acceptance work. Hardware budgets are admission estimates, and declared model revisions cannot attest the weights loaded by an external runtime. Failure simulations do not establish general prompt-injection resistance or OS-crash resilience.

Model weights and optional inference, search, transcription, browser, container, and CUDA training runtimes are separate installations. Android uses a development signing certificate; iOS distribution to physical devices requires Apple signing and provisioning. These artifacts are not Play/App Store releases.

Updater signatures are separate from Windows Authenticode and macOS notarization. Operating-system signing requires separately provisioned credentials. Local checks inspect archives and run hidden console engines; they do not launch desktop windows, installers, native dialogs, or simulators, and do not automate host input or focus. The older installed application remains untouched.

See [FEATURES_0_6.md](FEATURES_0_6.md), [COMPANION.md](COMPANION.md), and [RELEASE_OPERATIONS.md](RELEASE_OPERATIONS.md) for setup and supported scope.
