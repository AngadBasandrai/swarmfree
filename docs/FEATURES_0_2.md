# 0.2 feature guide

This guide describes implemented paths in the desktop alpha. [BUILD_STATUS.md](BUILD_STATUS.md) records what was actually validated. Some integrations require external runtimes/services and supported hardware.

The [0.3 feature guide](FEATURES_0_3.md) covers context/budgets, adaptive teams, worktrees, debugging, encrypted relays, multimodal conversion, packages and automation added after this guide.

## Projects, coding and permission controls

In **Studio > Projects & permissions**, select an existing directory, name the project and grant individual tools. New missions need both a selected project and **Enable granted project tools**. Agent **Configure** can narrow its permissions to a subset of the project's grants; an empty subset denies everything. Revocations take effect on subsequent tool calls. An already executing command may have side effects; use Stop to cancel it.

| Tool | Behavior |
| --- | --- |
| `workspace.list` | Bounded file/directory inventory within the selected project |
| `workspace.read` | Read bounded text from a relative path |
| `workspace.search` | Literal text search across eligible project files |
| `artifact.stage` | Propose complete text-file contents, store a diff and original hash |
| `checks.run` | Run one owner-named executable/argument array in the project |
| `research.fetch` | Retrieve public HTTPS HTML/text/PDF |
| `research.pdf` | Extract a relative project PDF's text with page markers |
| `research.search` | Search the configured SearXNG JSON server |
| `memory.search`, `memory.save` | Read/store project findings and decisions |
| `agent.delegate` | Create a bounded specialist child mission |

File tools reject absolute/escaping paths, symlink escapes and private folders such as `.git`, `.env*`, `.ssh`, `.aws`, `.venv` and `node_modules`. Files elsewhere in the project can still contain secrets; select the directory and grants accordingly. Named checks execute trusted project code with ordinary OS access and are **not an isolation sandbox**. They have a 90-second timeout, bounded captured output, no shell-string interpretation and cancellation of known process descendants. External servers/agents have their own OS permissions too.

Configure checks using explicit argument arrays, for example:

```json
{"tests":["python","-m","pytest","-q"],"build":["node","node_modules/vite/bin/vite.js","build"]}
```

Use the platform-appropriate executable; Windows batch wrappers may need a configured real executable instead. Grant `checks.run` and confirm trust in the commands/project. Agents cannot invent new allowed commands. No arbitrary command field is exposed to them.

Live agents receive a portable JSON tool envelope and the allowed schemas:

```json
{"tool":"artifact.stage","arguments":{"path":"src/example.py","content":"answer = 42\n"}}
```

The coordinator executes it, records the result, then asks the model to continue. This supports text-capable models without relying on provider-native tool-call formats. It does not guarantee a small model will follow the envelope. Missions default to eight tool operations per agent and four subtasks total, with delegation depth capped at two. Offline demos do not call tools.

Review proposed changes under **Studio > Review changes**. Applying verifies the on-disk file still matches the original hash; undo verifies it still matches the applied contents. Concurrent changes cause a conflict instead of silent overwriting. Applying a staged file is a separate owner action. Completed effect operations are reused on retry. After an uncertain interruption, inspect **Activity** and explicitly allow a retry only after reviewing what happened. Operation IDs are scoped to agent and arguments; a new branch deliberately has new effect identities.

**Stop all work** pauses active/queued missions, cancels owned inference/tool tasks and active model downloads. It does not undo prior command effects or terminate unrelated Ollama/extension programs. Projects, tool results, diffs and memory live locally in SQLite.

## Research and shared memory

Enable `research.fetch` for supplied URLs. Retrieval permits public HTTPS on port 443, checks all resolved addresses, pins a validated address for TLS, and validates every redirect (at most three). It blocks local/private networks and limits each source to 2 MB. This is HTTP retrieval, not browser automation; JavaScript-only pages and authenticated sites are unsupported.

PDF text extraction supports unencrypted files up to 100 pages, bounded decompression and text limits. Scanned PDFs need a separate OCR tool. Search needs an owner-configured SearXNG endpoint that enables `format=json`; the app does not operate a search service or assume an LLM key includes web search.

Retrieval stores source ID, URL/project path, date, content hash, text and producing agent/run. Models can cite `[source_ID](URL)`. The **Memory & sources** tab shows provenance. A retrieval record is not verification of a claim. Source text and files are presented as untrusted data to the model.

Owner notes tagged as decisions and relevant keyword matches enter future project missions. Completed agent work is saved with provenance. Memory is project-scoped; it is not a vector database or a promise of perfect long-term recall. Delete obsolete notes in Studio. Any selected remote/public model can receive the contextual text you allow in its mission.

## Architectures and continuation

The editor supports directed acyclic dependency graphs, bounded revision rounds, an upstream `contains` condition, terminal early-stop text and deterministic voting. Revision rounds repeat the graph with previous work available; arbitrary graph cycles are rejected. Architecture changes are pinned to new missions/branches.

Ten presets cover sequential handoffs, fan-out/join, supervisor, creator/critic, hierarchical delegation, blackboard memory, ensemble/voting, bounded revision and a single-agent baseline. Hierarchy and blackboard need the matching project tool grants to use delegation/memory. A vote reads upstream JSON `decision` strings and chooses a strict majority; ties return no decision. Agreement is not a truth check.

Specialist child missions appear in the normal mission history and obey inherited project permissions and the root subtask budget. **Branch through selected agent** retains that completed agent and its ancestors, then executes the remaining graph under a new run ID. The API also accepts a replacement architecture/route; reuse should be chosen only where node meanings remain compatible. It copies portable text work, not hidden model state or an external agent's session internals.

## Model doctor, routing, quota queue and Swarm lab

**Model doctor** explicitly sends one diagnostic request to the selected connected model. It tests streamed text, a small exact JSON task and observed latency. Context capacity is the configured ceiling, not a measured maximum; vision is not probed. A node requiring tested JSON only admits a current matching profile/model with a passing record. Probe a new model after changing its ID.

Auto routing filters permission, free eligibility, hardware fit, quota and tested requirements before ranking. It preserves an eligible current assignment, prefers fitting idle local inference, approved devices, eligible providers, then a busy local runtime, with observed latency helping within route classes. **Configure > Routing decisions** shows admission/skip reasons. Resident Ollama size can increase the conservative memory estimate; uncertain dedicated free VRAM is never treated as fully available.

Enable **Queue when capacity is unavailable** for bounded automatic retry after cooldown/no capacity. Pending queue state survives restart; the retry limit is 60. Non-transient credential/configuration errors remain interrupted for owner correction. **Low quota** serializes this mission's model calls and restricts it to one revision round; it does not reduce the number of nodes in its chosen graph. Request counters are local estimates and share real project scopes, including sibling keys.

**Swarm lab** runs the same task on the selected architectures plus a single-agent baseline. It records wall time, calls, estimated tokens, artifact count and owner scores from 0 to 5. Tokens are character estimates; provider-specific billing and quality are not inferred. Default lab mode is scripted demo. Live mode consumes the configured eligible routes; tools are disabled so the comparison cannot mutate the project. Live comparisons should be repeated on actual models before drawing conclusions.

## Extensions

Add a trusted MCP stdio process or HTTP server under **Studio > Extensions**. For stdio provide a JSON executable/argument array; its working directory is the selected project. Discover tools, then grant each `mcp.EXTENSION_ID.TOOL_NAME` in project permissions. Schemas are given to the model. HTTP bearer keys can be session-only and never enter record SQLite. The adapter negotiates MCP 2025-11-25, 2025-06-18 or 2024-11-05, supports bounded JSON/SSE responses and cleans up HTTP sessions. Newer revisions, resources/prompts, background tasks and implicit sampling are not implemented.

ACP v1 stdio profiles appear in model assignment. Explicitly grant `acp.EXTENSION_ID` in the project, enable mission tools, and disable free-only because the external agent manages its own model billing. Nodes with required verified model capabilities cannot use this unprobed adapter. Each task starts a fresh external session with portable work context; streamed agent text reaches the inspector. Extra client filesystem/terminal requests and permission prompts are denied. This does not prevent a trusted external binary from using its own internal OS capabilities. Tool approval semantics vary across agents; validate the chosen external program before relying on it. Free-only constrains inference routes, not the independent fees of owner-granted external tools.

## Devices, volunteer hub and sharding adapter

Pairing bundles include certificate trust and a short-lived invitation. Optional mDNS advertises a public origin/certificate fingerprint, never credentials. Discovered origins are untrusted hints; choose and verify a trusted owner. Direct private linkage still requires reachable TLS and manual firewall setup.

The self-hosted hub relays inference from approved outbound workers, issues separate revocable client keys, limits requests per minute/day and schedules by current queues/observed completed jobs. Worker time/day/battery limits reduce advertised capacity. This enables a small opt-in volunteer deployment; it is not a hosted global directory, payment system, audited reputation service or Byzantine consensus network. Completion counts do not establish answer quality. Volunteers/operators can see prompts. Public routes require consent on every mission. See [hub setup](HUB_QUICKSTART.md).

The experimental Exo adapter talks OpenAI-compatible chat to an already running loopback Exo runtime, suggested endpoint `http://127.0.0.1:52415/v1`. Distributed loading/sharding is performed by that runtime on supported machines. The app does not install Exo, join its network or combine arbitrary Windows GPUs. Physical sharding and supported-platform behavior were not validated here.

## Backups, migrations and releases

Create a backup in **Studio > Backups & updates**. Work pauses first; SQLite online backup captures record/checkpoint databases and a SHA-256 manifest. Credentials, project files and TLS private keys are excluded. Close the application before `swarm-engine restore BACKUP.zip --data-dir DATA_DIRECTORY`. Restore validates hashes/integrity and retains prior databases; replacement of the two files is sequential, so retain the backup and prior copies for recovery from a power failure mid-restore. Future unsupported schema versions are rejected.

An OS file lock prevents simultaneous writers to one workspace. The desktop updater accepts an operator-selected HTTPS manifest and trusted public key, verifies signed artifacts, backs up state, then installs/restarts only after the user's button click. This alpha has no published channel or signing credentials. Release scripts/CI can generate signed artifacts when configured; updater signing and OS code signing are separate. See [release operations](RELEASE_OPERATIONS.md).

## Primary references checked for this update

- [MCP lifecycle, 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle) and [server tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
- ACP v1 [initialization](https://agentclientprotocol.com/protocol/v1/initialization), [session setup](https://agentclientprotocol.com/protocol/v1/session-setup), [prompt turns](https://agentclientprotocol.com/protocol/v1/prompt-turn).
- [Tauri signed updater](https://v2.tauri.app/plugin/updater/) and [platform distribution](https://v2.tauri.app/distribute/).
- [Exo runtime source/setup](https://github.com/exo-explore/exo).
- [pypdf text extraction](https://pypdf.readthedocs.io/en/stable/user/extract-text.html), [cryptography certificate tutorial](https://cryptography.io/en/latest/x509/tutorial/), [Zeroconf API](https://python-zeroconf.readthedocs.io/en/latest/api.html).

Protocol/runtime support statements describe this implementation. Consult current upstream documentation before deploying external dependencies.
