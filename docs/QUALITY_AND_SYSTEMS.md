# Mission quality and managed systems

These controls belong to the downloadable desktop workbench. They use the private engine, persisted workspace and existing model/device adapters. A connected model is required for model-driven work.

## Mission contracts and recovery

Open **Mission brief & acceptance** when creating a mission. Define the objective, deliverables, constraints and prohibited actions. Required-text checks inspect the final output. The API also accepts `contains`, `excludes`, `json` and `min_length` checks. Textual constraints guide the team; project permissions continue to enforce available tools. Failed acceptance is saved as `acceptance_failed` with the failed checks.

In **Architectures**, **Lint graph** checks input/output types, missing selectors, unconnected required inputs, undeclared parameters, unavailable project tools, and bounded child composition. Supported quick fixes update the draft and remain reviewable before saving.

Each connection can run **On success**, **On error / timeout**, or **Always**. Set a stage timeout in the node inspector. Incoming outcomes can require any matching connection or every connection. A stage with a failure edge returns a sanitized structured error to the repair branch; global mission and branch budget failures stop execution. Failed external effects remain in their existing receipt ledger and are never automatically repeated by a repair edge.

## Reuse, assembly and checkpoints

Enable **Review similar completed tasks for reuse** to receive proposals from completed agents within the same mission family. Proposals require compatible project, privacy labels, tool grants, route, contracts and parameters. The similarity score is lexical, not a semantic correctness claim. Review proposals in **Artifacts & checkpoints**, select a contributor or run independently. Reuse validates the target output contract and records its source; it does not repeat the source agent's tool effects.

The artifact assembler accepts ordinary sections/fields, JSON objects, and table rows with unique `id` values. Object fields and table cells merge independently. Conflicting values require a contributor decision. The exported JSON includes the assembled fields, source selectors, content digests, conflicts and privacy labels. JSON object keys and table columns must support bounded field selectors. Different values from the same agent need distinct artifact field names.

The checkpoint timeline reads real persisted LangGraph checkpoints. **Fork here** creates a new execution, carries the selected checkpoint's completed results, and executes the remaining stages with fresh effect identities. Work after the selected checkpoint can make new model or tool calls. A checkpoint from a different frozen graph revision cannot be forked into the current graph. Comparison shows node output differences and budget usage.

## Experiments and promotion gates

Use **Control room → Quality** to run repeated tasks against a frozen architecture. Each selected agent or connection removal becomes a separate ablation variant. Tasks can check output contracts, required completed trajectory nodes and forbidden tools. All trials and their child missions share one experiment budget. Experiments support up to 60 trials, 12 tasks, five repeats and eight ablations; cancelling pauses the active trial, and restart marks incomplete experiments interrupted.

Approve a regression policy, select it, then run its repeated evaluation. Attach its gate ID to the architecture draft before saving. Promotion requires passing baseline evidence for that exact behavior and policy. Graph layout and descriptive architecture metadata do not invalidate evidence. Changing agent instructions, contracts, routes, tool grants or frozen child behavior does. Existing gates must be cleared explicitly before removing or replacing the policy. Checks establish only the properties they measure; a passing check is not a general correctness guarantee.

## Encrypted workspace sync

**Managed systems → Encrypted sync** exports selected architectures, personal agent blueprints, explicitly approved current knowledge, and run summaries. AES-GCM encrypts the bundle with a scrypt-derived passphrase key. Provider/device credentials, filesystem grants, active jobs and model weights are excluded. Credential-like text blocks export. Keep the passphrase outside the bundle.

Import requires local decryption and a preview. Conflicts default to skip; replacement is explicit. Applying checks the reviewed workspace state atomically and writes a replay receipt. Built-in presets and gated local designs cannot be overwritten through sync. Imported knowledge and histories are read-only review archives; their source project IDs are retained, and importing does not execute a mission or activate knowledge automatically.

Bundles are limited to 200 records / 1 MB plaintext. They can be exported/imported as files or sent through an existing trusted TLS coordinator connection with a project-scoped **sync** team credential. The receiver keeps an encrypted pending inbox until its owner reviews it. No passphrase is transmitted to the receiving endpoint.

## Verified runtime installation

**Managed systems → Inference runtimes** queries the official llama.cpp releases catalog and shows archives matching the current OS and CPU with upstream SHA-256 digests. Packages without a digest or matching platform are excluded. The workbench verifies downloads, bounds extraction, rejects traversal and links, and records every installed file's hash. Installation never executes package contents.

Activate a version, then register a local GGUF through **Register model with pinned runtime**. Managed model loads verify the entire pinned installation. Activation stops only workbench-owned runtime processes and updates managed models; rollback selects the previous installed version. CUDA/Metal/Vulkan driver compatibility, model licensing and model-specific runtime support still require an actual successful load on that host.

## Private vLLM cluster

**Managed systems → Private GPU cluster** controls workbench-owned vLLM multiprocessing containers through the existing authenticated worker channel. Each worker must separately opt in and be approved for clustering:

```text
swarm-engine worker --pair PAIR_BUNDLE --cluster-image REGISTRY/IMAGE@sha256:DIGEST --cluster-model-root /models --cluster-address 10.0.0.10 --vram-gb ALLOCATION
```

Supported placement uses 2–8 trusted Linux GPU workers with an installed digest-pinned image, identical complete safetensors model bytes, private IPv4 interfaces, live GPU memory telemetry, and 1–8 tensor-parallel GPUs per worker. Pipeline stages equal the worker count. Inspection verifies required CLI flags and hashes model files. Startup rechecks placement, probes the private master connection, and requires actual container/head health checks. The API server binds loopback on the head; inference travels through the authenticated worker lease channel. Select its `cluster:...` model on the head device after startup.

Only owned containers are stopped. Partial deployment is rolled back, and containers expire without coordinator renewals. TCP latency measures reachability, not usable tensor bandwidth. This adapter needs a trusted fast private network and a compatible model/runtime topology. Public WAN sharding, Windows GPU clusters, native cluster tool calls, and arbitrary remote-code models are excluded. Physical multi-GPU/multi-host validation remains a separate acceptance check; protocol fixtures do not establish performance or model compatibility.

Primary integration reference: [vLLM parallelism and scaling](https://docs.vllm.ai/en/stable/serving/parallelism_scaling/).

## Isolated extensions

**Managed systems → Extension isolation** runs local MCP or ACP commands inside an installed Linux Docker image pinned by digest. Configure commands as executables inside that image. The runner has no network, a read-only root and selected workspace snapshot, bounded temporary storage, CPU/memory/PID limits, an unprivileged UID, dropped capabilities and no new privileges. Private/configuration files and symlinks are excluded from selected files.

Explicit extension-vault grants are exposed as `/run/secrets/NAME` files with `NAME_FILE` environment pointers. Exact granted secret values are redacted from returned JSON protocol data. This does not prevent an adversarial extension from encoding a secret into its output; grant secrets only to extensions you trust with those values. Output provenance inherits selected source files. Cleanup removes the owned container after success, failure or cancellation. HTTP extension servers retain their own security boundary. There is no automatic host execution fallback.

`scripts/check_extension_sandbox.py` exercises actual Docker/MCP isolation only on an explicitly opted-in disposable CI runner. Unit tests exercise contracts, approvals and file integrity without launching host Docker or native app windows.
