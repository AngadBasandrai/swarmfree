# Swarm Workbench 0.4

The twelve additions are part of the downloadable desktop application. Most controls are under **Control room → Execution tools**. Each installation measures its own hardware; all paths, keys and resource allocations belong to that installation. MIT covers this project. Models and optional programs retain their own licenses.

| Addition | Controls and behavior |
| --- | --- |
| Task-aware routing | New swarm → Task-aware routing and native tools. Automatic routes start with smaller eligible models, or prefer measured task success. Failed output contracts trigger a bounded retry on another eligible route. Explicit assignments retry the chosen route. |
| Free-model catalog | Models & routing. Search local open-weight recommendations and refresh OpenRouter's current zero-price `:free` catalog. Inspect capabilities, license, dates, hardware estimates and upstream documentation before downloading or assigning. |
| Direct GGUF | Models & routing → Import a GGUF model. Choose an existing GGUF and trusted llama-server executable, declare the license and GPU layers, and import. Load, probe or unload there; assign a registered runtime in the mission or individual agent. |
| Reported usage | Mission budget, debugger call records and evaluation exports. Provider-reported input/output/cache counters replace token estimates for completed requests. Missing counts retain estimates; interrupted requests retain conservative reservations. |
| Semantic search | Semantic search. Select a project and an installed Ollama embedding model, build the index, then search concepts. Matches include paths, lines, similarity and stale markers. Agents can use the granted `semantic.search` tool. |
| Native calls and typed handoffs | Enable native function calling at mission creation. Architectures → selected agent → Typed handoff and validation defines an optional JSON schema and output checks. Arguments are validated before tools execute; validated JSON work products carry structured data across graph edges. |
| Browser agents | Browser agents. Install headless Chromium or select a trusted executable. Grant exact HTTPS origins per project, then open an owned browser for a mission agent. Read, click, fill and screenshot controls and action history are available. Model actions also need browser tool grants. |
| Execution workers | Execution workers. Opt in on a private worker with an installed Docker image, approve execution separately and grant the project access to that device. `remote.execute` invokes a configured named check. Returned edits are staged for review. |
| Device scheduling | Automatic routing considers queued jobs, measured free memory, installed model sizes, residency and task outcomes. Owned idle models can be unloaded when another admitted model needs space; inference stays serialized per local runtime/device. |
| Clarification inbox | Agent questions. Grant `user.ask`; questions suspend only the requesting agent. Answer with text or typed JSON, inspect the requested schema, cancel the question or open the mission. Pending questions survive restart. |
| Encrypted full recovery | Encrypted recovery. Create a passphrase-encrypted snapshot including databases, attachments, selected eligible project files and recoverable worktree contents. Preview destinations and project remappings before restoring into a separate empty directory. |
| Automated evaluations | Evaluations. Define tasks, deterministic checks, architecture/model routes and optional prompt variants. Run, cancel, inspect individual missions and export results. Recommend the smallest team passing every configured task, using token usage to break ties. |

## Routes, validation and usage

Routing preserves free-only admission, provider quota scopes, capability requirements and explicit public-worker sharing consent. Measured outcomes are local observations, separated by general, coding, research or tool-use category. Stream completion alone is marked unchecked. Tool results and configured contracts provide explicit measurements; they do not establish broad model correctness.

Agent checks support `contains`, `excludes`, `json` and `min_length`. JSON schemas support bounded nested objects/arrays and ordinary draft-2020-12 constraints; remote schema references and regex patterns are excluded. Apply the contract, then save the architecture. Failed outputs remain inspectable and are withheld from downstream agents. Three validation attempts are the default and consume the same mission budget. A fixed model can revise its output; automatic routing tries another admitted model. Turn off native tools when a selected model/provider does not support function calling to use the existing JSON tool protocol.

Native function names are mapped back to project tool names. Tool results use native assistant/tool wire roles when their complete call/result group fits in the retained window. Older groups compact into portable text; hidden model state is never transferred. The exact request inspector records tools and output schemas as well as messages.

The usage ledger retains estimates beside reported counters. Cache counts are a subset of input counts. Counters describe the provider/worker response; they are not an independently verified invoice. Spending reservations still use owner-declared input/output prices and remain conservative for failed or duplicate jobs. External tool fees are outside the ledger.

## Models and local resource handling

Ollama remains optional and supports its existing download, load/unload and allocation controls. Local catalog hardware figures are Q4-oriented estimates, not measured requirements. The source/model page and selected quantization take precedence. The cloud refresh requests the official OpenRouter model catalog; the embedded local metadata was checked on 11 October 2026. Account-specific Google/Groq models are discovered in Providers. Free tiers can change and are not unlimited.

GGUF import registers an existing file; it does not copy weights or download executable code. llama-server runs headlessly on a random loopback port, using a private session key file. Its built-in agent tools and web UI are disabled. The executable and model identity are checked before loading. Resource admission conservatively accounts for the whole model and context; GPU offload also needs sufficient measured VRAM. Only one managed GGUF process is resident at a time. Context allocation changes reload that process. Local Ollama and GGUF inference share a lock; switching runtimes releases owned resident models when necessary. Memory estimates are admission checks, not OS-enforced process caps.

Semantic indexing uses the local Ollama `/api/embed` API and needs an installed embedding model, for example `qwen3-embedding:0.6b`. Project text, retrieved documents and decisions are chunked and stored locally with vectors. Indexing is bounded to 1200 chunks and eligible UTF-8 files; private/configuration folders and symlink escapes are excluded. Rebuild after file changes or changing the embedding model. No cloud embedding fallback sends project contents elsewhere.

## Browsers and remote work

Browser profiles are created under this application's data directory. They do not attach to an existing desktop browser or use global input/focus control. At most two are open concurrently. Downloads, service workers, popups and WebSocket connections are blocked. Exact-origin grants allow GET/HEAD; interaction grants additionally permit changing requests such as POST. Add required asset origins separately. HTTPS addresses are checked for public IPs and pinned in the owned browser process; private networks and ungranted redirects are excluded. Site content remains untrusted data. Browser profiles can hold cookies; they are deliberately excluded from backups. Field values are omitted from the action ledger and redacted in the tool operation ledger; a model conversation may still contain values the owner or agent supplied.

Private execution requires three choices: the worker owner opts in to an installed Docker image, the coordinator approves that worker for execution, and the project grants that device access. Example:

```text
swarm-engine-windows-x64.exe worker --pair PAIRING_BUNDLE --execution-image YOUR_INSTALLED_IMAGE
```

The selected image must already exist; pulls and a host-execution fallback are disabled. The worker runs the project's configured argument array in an ephemeral mount with no network, dropped capabilities, a read-only container filesystem, and memory/CPU/PID limits. Transferred projects are capped at 100 UTF-8 files / 512 KB, with per-file and path checks. There is one active lease per worker. Cancellation, expiry and revocation fence out stale outputs; only validated returns are staged. Deletions, large files and binary files require manual review. Provider keys stay at the coordinator. Public hub workers do not gain private execution approval.

Questions remain durable if a mission is paused or the coordinator restarts. Resume the mission and answer the existing question to continue. Other independent graph branches can finish while an agent waits; downstream joins wait for its answer. Waiting counts toward the runtime ceiling. The inbox accepts ordinary task clarification and should not be used to collect API keys or passwords.

## Recovery and evaluations

A full backup requires idle missions, chats and background work. Automation and owner mutations are suspended while its database snapshots are taken. Encryption uses AES-256-GCM with a fresh nonce and salt and a scrypt-derived passphrase key; the passphrase is not stored. The archive checks member hashes, sizes and paths before restore. Current limits are 128 MiB of recovered content / 5000 files. Files above 16 MiB, private/configuration folders, secret-named files and key/certificate files are excluded; inspect the preview's exclusions. Model weights, browser cookies, the OS credential vault and Git metadata are not included.

Recoverable worktrees become ordinary recovered file snapshots, without machine-specific `.git` pointers. The restore remaps project, attachment, artifact and recovered worktree paths. Programs, automation and device approvals require explicit re-enabling; credentials must be reconnected. Unselected project directories need relinking. Existing data/files are never overwritten by full restore. An interrupted restore leaves an incomplete marker and cannot be started as a workspace. Keep the original backup and restore into a fresh directory.

To open a recovered desktop workspace yourself:

```text
swarm-workbench.exe --data-dir NEW_RECOVERED_DIRECTORY
```

The bundled offline command also supports encrypted previews and restores:

```text
swarm-engine-windows-x64.exe restore BACKUP.swarmbak --encrypted --preview --data-dir EMPTY_DIRECTORY
swarm-engine-windows-x64.exe restore BACKUP.swarmbak --encrypted --reviewed-sha256 PREVIEW_DIGEST --data-dir EMPTY_DIRECTORY
```

The CLI prompts for the passphrase; use `--mapping FILE.json` to remap project IDs. The original database-only ZIP restore remains supported.

Evaluation tasks use `contains`, `excludes`, `json`, `min_length`, and optional `named_check` rules. A named check references an owner-configured check, needs `checks.run` permission and consumes that agent's tool budget. It inspects the actual selected project/worktree; it does not apply staged edits automatically. Each compared mission receives the same supplied limits. Routes remain subject to free-only, spending and sharing policy. Demo evaluates the scripted harness; live mode evaluates actual selected models. Passing these checks only demonstrates the tested tasks. Restarted suites are marked interrupted; individual mission checkpoints remain available.

## Primary sources checked for this release

- [Ollama chat](https://docs.ollama.com/api/chat): native tools, schema formats and reported inference counters.
- [Ollama embeddings](https://docs.ollama.com/api/embed): local embedding requests and vectors.
- [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md): GGUF runtime flags, authenticated compatible API and built-in agent defaults.
- [Qwen3 model license](https://huggingface.co/Qwen/Qwen3-0.6B) and [Qwen3 embedding report](https://arxiv.org/abs/2506.05176): local catalog licensing and model families.
- [OpenRouter models API](https://openrouter.ai/docs/api/api-reference/models/get-models) and [free-tier pricing](https://openrouter.ai/pricing): catalog refresh and account limits.
- [Google API rate limits](https://ai.google.dev/gemini-api/docs/rate-limits): actual project/account quotas must be checked at the provider.
- [Playwright browser contexts](https://playwright.dev/python/docs/api/class-browsercontext) and [network interception](https://playwright.dev/python/docs/network): owned contexts and scoped request handling.

Verification uses owned fixtures and headless processes. See [BUILD_STATUS.md](BUILD_STATUS.md) for completed checks and external validation still required.
