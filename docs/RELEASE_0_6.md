Swarm Workbench 0.6.5 expands the downloadable desktop application's architecture library from ten to twenty-two presets and adds a new visual editor and bounded recursive teams.

- New patterns include map/reduce, routing and specialists, test-driven work, evidence auditing, a judged debate, red/blue teams, specialist reviews, diamond comparisons, and specify/build/verify.
- Recursive decomposition, critique/repair, and fork/join execute real child teams. Self-calls survive custom copies; upstream outputs can scope the next task. Depth limits and stopping markers switch to a connected model that solves the base case.
- Editing adds search/categories, a selectable team list, duplicate/remove, undo/redo, automatic layout, explicit connection controls, reference validation and execution previews. Child-team buttons and parent/root navigation make nested work inspectable.
- Shared root budgets, immutable snapshots, permission intersections and checkpoint reuse keep recursive work bounded. Four nested levels, 128 expanded nodes and a configurable 0–32 child-run limit are supported. Preflight includes nested model stages.

Missions, automations, campaigns, comparisons, and evaluations use connected models. If no eligible model is available, the mission reports missing capacity or waits when the owner enabled queuing. The offline demonstration mode remains removed.

Older demonstration records remain available for inspection and export. Resuming, branching, or chatting with those records is disabled. Onboarding now guides owners toward a provider, local model, or linked device. Automated checks use owned provider fixtures outside the production app.

This release retains the twelve command-center features introduced in 0.6:

- Project campaigns with dependencies, shared budgets, acceptance review and restart recovery.
- Routing feedback, architecture preflight with shared quota estimates, and versioned project knowledge.
- Explicit local voice input, reviewed MCP Apps panels and isolated Docker notebooks.
- Optional local LoRA training, evaluation-gated promotion, rollback and measured power scheduling.
- Live model compatibility packs and a separate native Android/iOS companion.

Download the desktop installer or portable package for your platform. Model weights and optional inference, transcription, Docker and training runtimes are separate installations. Notebook code requires an already installed container image. The training backend requires a separate CUDA environment and downloaded local weights.

The Android companion development APK supports sideload testing. The iOS build targets the simulator; physical iOS distribution needs Apple signing and provisioning. These builds do not imply Play/App Store publication. Provider keys remain on the coordinator, and phone access uses scoped, revocable credentials.

Updater signatures and SHA-256 checksums accompany desktop assets. OS signing/notarization depends on separately provisioned publisher credentials. See [the architecture guide](https://github.com/AngadBasandrai/swarmfree/blob/v0.6.5/docs/ARCHITECTURES.md), [the feature guide](https://github.com/AngadBasandrai/swarmfree/blob/v0.6.5/docs/FEATURES_0_6.md), [companion setup](https://github.com/AngadBasandrai/swarmfree/blob/v0.6.5/docs/COMPANION.md) and bundled BUILD_STATUS.md for supported scope and validation.
