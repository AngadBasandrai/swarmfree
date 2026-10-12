# Swarm Workbench 0.8 desktop alpha

This release adds twelve capabilities to the native downloadable workbench:

1. Structured mission briefs with final-output acceptance checks.
2. Architecture linting with reviewable fixes.
3. Failure and timeout edges for repair/fallback/review stages.
4. Owner-reviewed duplicate-task reuse across a mission family.
5. Provenance-aware section, JSON-object and table-cell assembly.
6. Repeated contribution experiments with agent/edge ablations.
7. Regression policies that gate architecture promotion.
8. Real saved-checkpoint forks and output/usage comparisons.
9. Selective encrypted workspace sync with conflicts and replay receipts.
10. Verified llama.cpp runtime installation, version pinning and rollback.
11. Opt-in private vLLM multiprocessing deployment on linked Linux GPU workers.
12. Isolated local MCP/ACP execution with selected files, explicit secrets and bounded resources.

See [QUALITY_AND_SYSTEMS.md](QUALITY_AND_SYSTEMS.md) for controls, supported scope and validation limits. The existing 22 architecture presets, live recursive tree, editor, individual agent conversations, free-account profiles, local model admission and private workers remain available. Mission execution requires connected real models.

Physical distributed GPU performance and model/topology compatibility require separate hardware acceptance. Container isolation is tested on disposable Docker CI, while local native checks avoid host windows and host input control. Platform packages carry updater signatures; operating system code-signing/notarization depends on separately provisioned certificates.
