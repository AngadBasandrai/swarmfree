# Authorized 0.6 work

The user requested all twelve additions on 2026-10-12. Deliver these in the downloadable desktop application, with a separate packaged mobile companion. Do not launch graphical applications or control host input during development.

1. Mission command center, linked actions and device capacity.
2. Durable, dependency-aware project campaigns with aggregate budgets and acceptance review.
3. Feedback-based routing with visible evidence and version isolation.
4. Preflight comparison without inference calls.
5. Versioned project knowledge and dependency invalidation.
6. Native Android/iOS companion using scoped, revocable team credentials.
7. Explicit microphone capture, editable local transcription and speech playback.
8. Negotiated MCP Apps resources, isolated rendering and permission-checked interactions.
9. Reviewed, container-executed notebooks with reproducible inputs and outputs.
10. Reviewed training datasets, optional local LoRA backend, evaluation and rollback.
11. Measured power/temperature scheduling, quiet hours and energy limits.
12. Live compatibility workflow suites and sanitized, opt-in export/import of reports.

Acceptance: meaningful backend integration tests; headless desktop workflows; native request boundary checks; frontend/native compilation; frozen-engine and downloadable package validation. Mobile builds require platform SDKs; live model, microphone, GPU and physical phone checks must be reported separately from fixture tests. OS signing and store publishing require publisher credentials and must not be claimed without them.

Implemented all twelve. Source verification: 137 Python tests, 26 headless UI flows, frontend/native builds and native boundaries pass. Windows installer/portable/worker payload and frozen-engine checks pass, including owned HTTPS one-use pairing and revocation. Android and iOS simulator builds pass; Android signatures/native payload and simulator archive permissions are checked. Optional CUDA/Docker/microphone/physical phone/live-model acceptance remains separate. Current evidence is recorded in BUILD_STATUS.md.

Published 0.6.3 with all four desktop platforms, Android development APK and iOS simulator archive. Source and release build workflows pass; public feeds, package signatures/hashes, phone provenance and 26 checksum entries verify. See BUILD_STATUS.md for current evidence and external hardware acceptance.
