Swarm Workbench 0.6.2 adds all twelve command-center features to the downloadable desktop application:

- Project campaigns with dependencies, shared budgets, acceptance review and restart recovery.
- Routing feedback, architecture preflight with shared quota estimates, and versioned project knowledge.
- Explicit local voice input, reviewed MCP Apps panels and isolated Docker notebooks.
- Optional local LoRA training, evaluation-gated promotion, rollback and measured power scheduling.
- Live model compatibility packs and a separate native Android/iOS companion.

Download the desktop installer or portable package for your platform. Model weights and optional inference, transcription, Docker and training runtimes are separate installations. Notebook code requires an already installed container image. The training backend requires a separate CUDA environment and downloaded local weights.

The Android companion development APK supports sideload testing. The iOS build targets the simulator; physical iOS distribution needs Apple signing and provisioning. These builds do not imply Play/App Store publication. Provider keys remain on the coordinator, and phone access uses scoped, revocable credentials.

Maintenance fixes make microphone Stop release pending startup resources immediately and prevent a cancelled startup from replacing a newer recording. They also fix an ambiguous dependency-card selector in the UI tests and give graph fixtures enough time on loaded runners.

Updater signatures and SHA-256 checksums accompany desktop assets. OS signing/notarization depends on separately provisioned publisher credentials. See [the feature guide](https://github.com/AngadBasandrai/swarmfree/blob/v0.6.2/docs/FEATURES_0_6.md), [companion setup](https://github.com/AngadBasandrai/swarmfree/blob/v0.6.2/docs/COMPANION.md) and bundled BUILD_STATUS.md for supported scope and validation.
