# Alpha build status

11 October 2026 · version 0.5.0 · MIT

The downloadable application adds the twelve capabilities described in [FEATURES_0_5.md](FEATURES_0_5.md): guided setup and verified downloads, graph design, typed reusable sub-swarms, bounded channels and task claims, opt-in pure response reuse, team credentials, outbound data reviews, failure fixtures, A2A text tasks, experimental Exo placement, signed community packages and public release infrastructure. Previous 0.4 execution, browser, evaluation and recovery capabilities remain included.

Source validation completed locally:

- 119 Python checks pass. New checks cover team roles/revocation, redacted outbound review, provenance, cache invalidation, graph snapshot/cycle rejection, shared root sub-swarm budgets, mailbox membership/ownership, A2A 1.0/0.3 request shapes and task boundaries, Exo topology/placement hash review, package signatures/hash tampering, verified downloads/private destination rejection and all eight controlled failure scenarios.
- 18 headless Chromium UI flows pass, including setup, architecture preview/save, every new panel and a disconnected-stream simulation.
- TypeScript/Vite production build, cargo check --locked, three standalone Rust request-boundary checks and Python dependency consistency pass.

Windows installer, portable and worker package checks target the frozen 0.5 engine with development tools removed from PATH. They cover startup, authentication, saved graphs, native PDF calls, token reporting, GGUF metadata, browser-driver bundling, recovery, CLI modes and the new setup/design/team/policy/failure services. Installer contents are inspected as archives without local installation. Current build execution and output artifacts are recorded in the release notes.

Public source/release target: https://github.com/AngadBasandrai/swarmfree. Registry/update metadata target: https://swarms.grafitelab.in. Protected Ed25519 and Tauri publisher identities are prepared; public pins ship in source. Tagged CI publication requires all signed platform artifacts. DNS remains an owner setup step; see [PUBLIC_HOSTING.md](PUBLIC_HOSTING.md).

Updater signatures are separate from operating system code signing. Windows Authenticode and macOS notarization require real certificate credentials; they are optional workflow inputs and are not claimed by local validation. Clean-runner Windows installation acceptance runs only in disposable GitHub CI. Remote Windows/macOS/Linux results must be checked on the workflow run before claiming those packages validated.

Tests use owned protocol fixtures and scripted inference. No live provider keys, model weights, second physical device or Apple Silicon Exo cluster were used. A2A streaming and Exo deployment need acceptance against actual target services. HTTP transfer measurements are not tensor interconnect benchmarks. Failure-lab reconstruction is not an OS crash and does not prove general prompt-injection resistance.

Manual native dialogs/notifications, real model allocation/performance, physical workers, public hubs/relays, real runtime/container installations and signed update installation remain external acceptance work. No desktop application, installer or native dialog is opened for local verification; there is no desktop input/focus automation. The earlier 0.1 installation remains untouched.
