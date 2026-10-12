# Swarm Workbench 0.8.0 alpha

Downloadable native desktop workbench for Windows x64, macOS Apple silicon/Intel, and Linux x64. End users need no Python, Node.js or Rust.

The release adds mission briefs, graph linting and failure branches; reviewed task reuse and provenance-aware artifact assembly; repeated ablations, promotion gates and checkpoint comparisons; encrypted workspace sync; verified inference runtime installation with rollback; private vLLM cluster controls; and isolated local MCP/ACP extensions.

Open **Control room → Quality**, **Control room → Managed systems**, and each mission’s **Artifacts & checkpoints** tab. Mission briefs appear in the new-mission composer, and graph diagnostics/outcome controls appear in the architecture editor. The 22 presets and bounded recursive architecture workshop continue to use actual connected models.

See bundled `docs/FEATURES_0_8.md` and `docs/QUALITY_AND_SYSTEMS.md` for controls, limits and integration references. Private model sharding requires explicitly approved trusted Linux GPU workers and a compatible model/runtime/network; physical multi-host performance validation is separate from protocol tests. Extension isolation requires an installed digest-pinned Docker image.

Release artifacts carry updater signatures and SHA-256 checksums. Operating system code signatures/notarization depend on separately provisioned certificates. Model weights, optional inference runtimes and container images are separate downloads. Android assets are development APKs; iOS assets target the simulator.
