# Swarm Workbench

A downloadable desktop workbench for persistent agents using your own model providers, local Ollama models, linked devices, and opt-in volunteer inference. Each installation measures its own hardware and owns its own workspace. MIT licensed.

**0.5.1 Windows x64 alpha:** a Tauri desktop app with a bundled engine. End users need no Python, Node.js or Rust. Browser agents use an optional headless Chromium download; the desktop UI uses the operating system's webview. Clean-runner workflows build Windows, macOS (Apple Silicon and Intel), and Linux. Remote package validation status is recorded in the build notes. See [build status](docs/BUILD_STATUS.md), the [0.5 feature guide](docs/FEATURES_0_5.md) and the [research/design plan](docs/SWARM_WORKBENCH_PLAN.md).

## Downloads

- [Release downloads](https://github.com/AngadBasandrai/swarmfree/releases): select your platform's installer. Windows installs for the current user; launch from the Start menu.
- Portable desktop ZIP: extract the entire folder and open `swarm-workbench.exe`. Keep the companion engine and resources alongside it.
- Worker/server ZIP: run the bundled engine's `worker`, `coordinator`, `hub`, `relay`, or `restore` command. [Worker setup](docs/WORKER_QUICKSTART.md), [hub setup](docs/HUB_QUICKSTART.md).
- Every published release includes `SHA256SUMS.txt`. Source builds write local packages into `releases/`.

Local artifacts are in `releases/`; the public source and release target is [AngadBasandrai/swarmfree](https://github.com/AngadBasandrai/swarmfree). Updater signatures are configured; operating system signing requires separate credentials. The installer provisions Microsoft's WebView2 runtime if missing, which needs internet access. The portable version requires WebView2 already installed. Ollama, model weights, search servers, MCP servers and ACP agents are optional separate installations.

## Capabilities

- Task-aware routing and output validation, a dated free-model catalog, managed GGUF runtimes, reported token accounting, local semantic indexing, native function calls and typed work products.
- Owned headless browser agents, opt-in private Docker execution workers, resource-aware model scheduling, durable agent questions, encrypted full recovery with path remapping and automated team/model/prompt evaluations.

- Automatic context compaction, editable handoff summaries and exact next-request previews; mission-wide time/request/token/estimated-spend ceilings and quota observations.
- Adaptive planner/specialist teams, measured local-model tuning, idle unloading, per-agent Git worktrees and optional containerized checks with reviewed integration.
- Execution debugger, stage replay, linked-worker trace export, encrypted outbound device relays and QR pairing; worker health, fair admission, fenced reassignment and optional two-worker comparisons.
- Evidence passage review, image/text/PDF attachments, explicit scanned-PDF OCR and audio transcription, versioned swarm packages, schedules/file/Git triggers and an approval inbox with opt-in native notifications.
- Live agent graph, clickable agent bar, individual conversations, model switching, portable work products, pause/resume, SQLite checkpoints, restart recovery, and branching through a selected agent.
- Ten architecture presets: supervisor, pipeline, parallel/join, creator/critic, baseline, handoff, hierarchy, shared blackboard, ensemble/voting, and bounded revision. Visual editing and JSON import/export, up to 16 graph nodes and five revision rounds; conditions, early stopping, and capped specialist subtasks.
- Coding tools: scoped project reads/search, proposed file contents with diffs, conflict-checked manual apply/undo, and explicitly allowed named checks. A persistent tool-operation ledger prevents completed effects being repeated after a model handoff.
- Research tools: public HTTPS retrieval, local/remote PDF text extraction, configurable SearXNG search, dated source records and citation IDs. Project memory stores decisions, evidence and completed work across missions.
- Google Gemini, Groq, OpenRouter, Mistral, compatible endpoint, freellmpool, volunteer hub, and experimental Exo adapters. Multiple own-account profiles, shared quota scopes, cooldown-aware queuing, conservative free-only routing, measured capability filters and routing explanations.
- Actual RAM inventory, NVIDIA NVML, Linux AMD/Intel memory metrics where exposed, Windows DXGI capacity, and Apple unified-memory accounting. Per-device budgets, Ollama inventory/download/load/unload, observed resident allocations and conservative fit estimates.
- Private workers with TLS, one-use invitations, owner approval, scoped/revocable credentials, leased streaming jobs and stale-result fencing. Certificate generation, certificate-bearing pairing bundles and optional LAN discovery.
- Self-hosted volunteer hub with client keys, RPM/day budgets, outbound workers, availability scheduling, contribution hours, battery pause and daily job limits. Public routes require explicit consent for each mission.
- Swarm lab compares architectures with a single-agent baseline, call/time/token estimates and manual quality scores. Model doctor probes streamed text, JSON and latency.
- Opt-in MCP stdio/HTTP tools and ACP v1 stdio agents. Per-project and per-agent permissions; visible operations; emergency stop.
- Paired-database backups, offline integrity-checked restore, schema migrations, single-writer workspace locks, and a configurable signature-verifying native updater. No update channel is preconfigured.

The [0.4 feature guide](docs/FEATURES_0_4.md) explains the twelve additions, setup and practical limits; the [0.2 guide](docs/FEATURES_0_2.md) covers the earlier tools. Offline demo responses are explicitly scripted; they are not LLM output. Named checks and external extensions run with your OS permissions unless you explicitly configure the supported container wrapper for checks. OCR/transcription programs are separate trusted installations.

## First run

1. Open **Providers** to link your own key, model and real quota scope. Alternatively install/start [Ollama](https://ollama.com/download) and use **Devices** to select models and set resource budgets.
2. For coding or research, open **Studio > Projects & permissions**, select a project directory and grant the tools it needs. Configure named checks or a search server only when needed.
3. Create a mission, choose its project, architecture and preset, and enable the project's tools. Public inference consent is off by default. With no connected models, select **Offline demo**.
4. Click any agent to chat, inspect routing or change its model/permissions. Completed work remains portable. Partial interrupted responses remain drafts and are excluded from the next model's history.
5. Review proposed edits under **Studio > Review changes** before applying them. Use **Memory & sources**, **Activity**, **Swarm lab** and **Model doctor** to inspect what happened.
6. Use **Control room** for automations, packages, tuning/media, evidence, quotas and encrypted device links. **Execution tools** contains model routing/GGUF, semantic search, owned browser agents, private execution workers, agent questions, evaluations and encrypted recovery. The mission inspector's **Context** and **Debugger** tabs explain what each agent sends and how to replay a stage.

Changing the model of a working agent pauses the graph and retries its current turn with the new assignment. Completed upstream work survives. Follow-ups to finished agents preserve the original graph outputs. **Branch through selected agent** creates an independent continuation with a new run ID.

## Accounts, quotas and free inference

Each owner links their own provider keys. Profiles are descriptive account/project metadata, not Google OAuth sign-ins. Credentials use the OS vault, or explicit session-only storage when selected; they do not enter SQLite or UI responses.

[Gemini limits are per project](https://ai.google.dev/gemini-api/docs/rate-limits), not per key. Give every key from one project the same scope ID. Missing scope IDs conservatively share one group per provider; the smallest configured RPM limit applies. A 429 cools down the whole group. Adding sibling keys does not increase that project's allowance. IDs are owner-declared, and local counters cannot see usage outside this application.

**Free-only** admits fitting local/private-device models and supported direct profiles the owner has confirmed free. OpenRouter permits `openrouter/free` or `:free` model IDs. Compatible and freellmpool gateways are excluded because hidden downstream pricing cannot be verified. No software can guarantee a zero bill for an account with billing enabled. Local inference consumes electricity and hardware; volunteered capacity is availability-dependent.

Install [freellmpool](https://github.com/0xzr/freellmpool) separately and add its actual loopback `/v1` endpoint. This adapter uses model discovery and streamed chat; the gateway manages its own credentials and downstream routes. Disable free-only when intentionally using an unverified gateway. Keys are never shared between unrelated users.

## Hardware and distributed execution

Ollama defaults to `http://127.0.0.1:11434`. `SWARM_OLLAMA_URL` may select another loopback URL. Pair a worker for another machine; avoid exposing unauthenticated Ollama over the internet.

Budgets are admission estimates, not hard OS allocation caps. Fit includes weights, overhead, rough context/KV allowance, other resident models and observed runtime sizes when available. Dedicated GPU admission requires actual free-memory telemetry; a Windows capacity reading alone is insufficient. Apple CPU and Metal use one shared RAM pool. Driver inventory does not prove that Ollama supports acceleration on that GPU. CPU fallback is available.

Private workers move whole inference requests between machines. They do not merge VRAM or split weights. The experimental **Exo** profile connects to a separately configured distributed runtime over a loopback OpenAI-compatible endpoint; this app does not bootstrap shards. Real sharding depends on that runtime, supported hardware and topology.

Use **Devices > Connection settings** to generate/select TLS certificates, configure a reachable origin and optionally advertise LAN discovery. Restart after saving. A direct pairing bundle includes the public trust certificate and one-use invitation; it includes no TLS private key. Workers must be approved before serving jobs. **Control room > Internet links & workers** also pairs through an optional self-hosted encrypted relay, with both endpoints connecting outbound. WAN volunteers can connect to a self-hosted [hub](docs/HUB_QUICKSTART.md). These services need a reachable HTTPS host; no hosted directory or global trust service is supplied.

## Build and check

Developers need Python 3.11+, a supported Node.js version, Rust and the platform's [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/). Windows also needs Visual Studio C++ build tools. Tested here with Python 3.13, Node 24 and Rust 1.96.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -e '.[dev,desktop]'
npm ci
.venv/Scripts/python scripts/build_desktop.py
```

On macOS/Linux use `.venv/bin/python`. The script freezes the engine, collects native/frontend dependency licenses and builds artifacts into `releases/`. `--bundle-only` reuses compiled binaries; `--package-only` refreshes archives/hashes. The manual GitHub workflow builds each OS and accepts optional signing secrets. It uploads artifacts without publishing a release. See [release operations](docs/RELEASE_OPERATIONS.md).

```powershell
.venv/Scripts/python -m pytest -q
.venv/Scripts/python scripts/check_native_requests.py
npm run build
npm run test:ui
.venv/Scripts/python scripts/check_desktop_packages.py --seven-zip "C:/Program Files/7-Zip/7z.exe" --check-engine
```

UI checks run headless. Package checks inspect installer archives and start only the hidden console engine; they do not execute installers, open desktop windows or manipulate desktop input. Automated model tests use mocks/scripted inference. Live credentials, hardware and platform validation remain separate.

Desktop state uses Tauri's app-data directory for `app.swarmworkbench.desktop`, on Windows `%LOCALAPPDATA%/app.swarmworkbench.desktop`. `SWARM_DATA_DIR` can select an isolated workspace. One coordinator owns each directory through an OS lock. Database backups exclude project files, attachment/worktree files, vault contents and TLS private keys; preserve those separately. The native owner token stays in IPC and rotates on restart.

`npm run desktop:dev` opens the native development app when manually invoked. The optional `python -m swarm.cli` plus Vite/browser transport is for development and headless tests. A Python wheel remains available via `scripts/package_release.py`.

The source is [MIT licensed](LICENSE). Bundled dependencies and fonts retain their own licenses in `notices/`. No models, provider credentials or central service keys are bundled.

Public registry and updater hosting use `swarms.grafitelab.in`. [Domain and publishing setup](docs/PUBLIC_HOSTING.md). Team & setup in Control room includes architecture design, reusable teams, bounded agent channels, project-scoped team roles, outbound data reviews, response reuse, failure fixtures, A2A text agents, Exo placement and signed package imports.
