# Swarm Workbench 0.3 desktop alpha

The twelve additions below extend the [0.2 features](FEATURES_0_2.md). This is a downloadable Tauri application with a private engine, not a hosted website. All device/model settings refer to the person running the installed application.

| Addition | Where to use it | Implemented behavior and practical limits |
| --- | --- | --- |
| Context management | Agent inspector → Context | Conservative window meter, extractive summaries, editable handoffs and exact portable-message preview. Old messages stay in local history. Compression is not an LLM understanding/accuracy guarantee; large tasks and summaries can be shortened. |
| Mission budgets | New mission → Budgets; running mission → Mission budget | Root-wide request/token/estimated-spend reservations include retries and specialists. Active-time timeout, limit editing while paused, budget-exceeded status. Token counts and owner-declared prices are estimates, not actual billing. External programs/tools can have independent fees. |
| Adaptive teams | New mission → Team execution | Planner starts alone and can finish directly or request deduplicated specialists; resources/speed/quality policies cap specialists at 1/2/4. Synthesis receives actual work. Root depth/subtask/budget limits apply. Identical revision outputs stop a stalled loop. No guarantee that a model produces a valid plan or improves quality. |
| Hardware tuning | Control room → Hardware & media | Explicit short Ollama benchmarks for up to three installed model tags and three context allocations. Shows measured token speed, placement, tag quantization and a recommendation. Local concurrency remains one; this is a speed sample, not a maximum-context/quality benchmark. It does not quantize or download weights automatically. |
| Separate coding environments | New mission → Git worktrees; Studio → Project permissions | Clean Git repositories get a detached worktree per agent. Apply staged changes there, then collect text changes for owner-reviewed integration. Integration checks the base contents for conflicts. Optional preinstalled Docker image runs named checks with no network, a read-only container root and resource limits. No silent OS fallback. Worktrees are not an OS sandbox. |
| Execution debugger | Mission → Debugger | Request timeline, exact sent messages/output limits, tools, handoffs, child calls and linked-worker timing. Rerun a selected stage in a new branch, optionally stopping there. Export an OTLP-style JSON trace. Replay makes fresh calls/effect identities; inspect effects before replaying. |
| Internet device links | Control room → Internet links & workers | Optional self-hosted outbound HTTPS mailbox relay with AES-GCM endpoint encryption, scoped credentials, QR bundles, retry/reconnection and diagnostics. Relay sees metadata, not decrypted inference. Direct TLS/mDNS pairing remains available. No hosted relay, automatic firewall changes, audited anonymity or physical-network validation is provided. |
| Worker reliability | Internet links & workers; volunteer hub | Completion/latency health samples, prompt-free usage receipts, bounded FIFO admission, idle-worker selection and one reassignment after a failure/expired lease before any output. Optional two-worker normalized-text comparison uses two jobs; agreement is not correctness. |
| Evidence review | Control room → Evidence review | Candidate statements extracted from final outputs, source text display, exact-passage checks, owner judgments and conflict links. Unsupported/unreviewed claims stay visible. This checks quotation provenance and records human review; it does not independently establish factual truth. |
| Multimodal work | Mission attachments; Hardware & media; Studio → Model doctor | PNG/JPEG/WebP images, UTF-8 text/PDF attachments, explicit local OCR, up to ten scanned-PDF pages and audio transcription through trusted installed command arrays. Optional red-image vision probe gates image routing. External OCR/transcription/render tools and their model weights are not bundled. |
| Reusable swarm packages | Control room → Swarm packages | Versioned graphs, instructions, requested permissions and evaluation tasks/rubrics. Preview and hash-check before import. Imports create fresh architecture IDs and never grant tools, link keys or run commands automatically. JSON file exchange; no hosted package marketplace. |
| Automation | Control room → Automation & inbox | Interval, daily UTC, bounded file-change and Git-change triggers; debounce, durable pending approvals, optional explicit automatic mode and notifications. Works while the engine runs. Stop all pauses automatic triggers persistently. Native notifications are opt-in and platform dependent. |

## Mission limits and model prices

Default limits: 30 minutes of active time, 100 model requests, 200,000 estimated tokens and a zero-dollar estimated spending ceiling. Local/demo/private-device inference has no provider model charge in the ledger. Confirmed free routes can run under the zero ceiling. A paid/unknown route needs declared input/output prices or the owner's explicit selection of no spending ceiling. Configure declared prices under **Providers → Declared model prices**; changing model IDs clears old prices. A zero declared price is an owner assertion, not a billing guarantee.

Tokens use two characters per token plus framing, with 2,048 tokens reserved per image. Root reservations avoid simultaneous children independently spending the same allowance. Interrupted/uncertain requests remain charged conservatively; no request is treated as automatically free because it failed. The provider's output parameter bounds generation where supported. Actual tokenizer and billing behavior may differ from estimates. ACP/external programs cannot have their independent internal spending enforced by this engine.

Paused and capacity-queued time is excluded from normal active-time accounting. A crash conservatively includes the time until restart in the last active interval. Increase reviewed limits and resume after a budget stop. Backup retains database records but excludes attachment files, worktrees and credential stores; back those files up separately.

## Local conversion examples

Install/trust the programs and any model files yourself. Configure argument arrays, not shell command strings. Placeholder expansion supports `{input}` and `{output}` only.

```json
{
  "ocr_argv": ["tesseract", "{input}", "stdout"],
  "pdf_render_argv": ["pdftoppm", "-png", "-scale-to", "1600", "-f", "1", "-l", "10", "{input}", "{output}"],
  "transcribe_argv": ["whisper-cli", "-m", "PATH_TO_MODEL", "-f", "{input}", "-otxt", "-of", "{output}"],
  "trusted": true
}
```

Programs have ordinary OS privileges and a command timeout. Conversions run as background jobs, display their status and can be cancelled with Stop all; interrupted jobs remain visible after restart. Audio formats depend on the installed transcriber; whisper.cpp commonly needs a compatible WAV input. PDF OCR renders at most ten PNG pages per conversion. The app checks output size and adds page labels; OCR errors remain possible. Upload/convert first, then use **Choose an existing attachment** in the mission composer. Raw images need a passing vision probe and enough context (usually at least 8K under this conservative image allocation).

## Separate worktrees and integration

Commit or stash the selected repository's changes first; the app does not do that for you. File tools/checks use the agent's working directory after the worktree is created. Staged patches apply there, leaving the main project for a separate review. **Stage changes for integration** collects eligible text files; review those integration artifacts in Studio before applying. Deletions/binary files and arbitrary Git merge conflicts still need manual Git review. Worktrees stay on disk for inspection; no automatic destructive cleanup occurs.

Container images must already exist. The configured image mounts the selected directory at `/workspace`, disables networking, drops Linux capabilities and limits memory/CPU/process count. A mounted project is writable and its trusted commands may alter it. For strict separation select worktrees too; container runtime/host security is a separate trust boundary.

## Private relay deployment and reconnection

Extract the Worker ZIP; its engine also serves the relay CLI. Provision HTTPS with your own certificate and reachable hostname. The listener defaults to port 9443; keep the operator credential private.

```powershell
./swarm-engine-windows-x64.exe relay --data-dir RELAY_DATA --host 0.0.0.0 --tls-cert CERT.pem --tls-key KEY.pem
```

Enter the HTTPS origin and relay operator credential in Control room. The operator key is used for provisioning, not sent to the paired worker or stored in record SQLite. Channel credentials/encryption keys use the selected OS/session vault. Each channel expires after 30 days; encrypted envelopes expire after two minutes. Pair within five minutes and approve the worker in Devices. QR codes encode the same private bundle; this CLI worker does not operate a camera scanner.

After persistent pairing, restart the worker without repasting an expired invitation:

```powershell
./swarm-engine-windows-x64.exe worker --coordinator https://YOUR_RELAY --relay-channel CHANNEL_ID
```

Endpoint clocks should agree. Session-only credentials cannot reconnect after process exit. If a relay channel expires, is revoked or credentials disappear, pair again. The relay supports bounded encrypted messages for inference; it does not expose owner APIs, forward arbitrary network calls or transfer project execution/provider keys. Protect the pairing bundle, which carries the shared encryption key. This transport has automated fixture coverage but has not had an independent security audit or physical internet-device test.

## Volunteer checks and receipts

The hub limits active client requests, admits waiting work FIFO and selects idle compatible workers using observed health. A dead job can be reassigned once before any streamed text; partially emitted outputs fail rather than being combined with a new model's answer. Workers renew fenced leases, so an expired contributor cannot overwrite the replacement result. Receipts retain model/status/time/output-count metadata, not the prompt. The coordinator's normal job records can still contain prompts.

Enable **Request two-worker comparisons** on a community profile or send `duplicate_check: true` to the hub chat API. It requires two distinct online workers and charges two jobs against hub/mission allowances. Outputs are buffered, compared after whitespace/case normalization and annotated with agreement/disagreement. Hub operators can inspect the complete comparisons; this is an exact-output diagnostic, not a semantic judge, attestation, monetary credit or resistance to colluding workers.

## Release acceptance

The manual build workflow can provision an optional Windows signing PFX and run silent install/installed-engine/uninstall checks on disposable GitHub-hosted Windows runners. The check script refuses this developer desktop. Workflow configuration is implemented; remote jobs were not executed here. Signing needs a real operator identity/certificate. Locally produced installers remain unsigned unless that identity is configured. Native notification delivery, actual signed update installation and physical-device/live-model behavior still require their respective environments.

## Primary implementation references

- [Ollama vision payloads](https://docs.ollama.com/capabilities/vision) and [chat timing/residency fields](https://docs.ollama.com/api/chat).
- [Git worktrees](https://git-scm.com/docs/git-worktree) and [Docker run options](https://docs.docker.com/reference/cli/docker/container/run/).
- [Cryptography authenticated encryption](https://cryptography.io/en/latest/hazmat/primitives/aead/) and [python-qrcode](https://github.com/lincolnloop/python-qrcode).
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp), [Tauri notifications](https://v2.tauri.app/plugin/notification/) and [OpenTelemetry trace concepts](https://opentelemetry.io/docs/concepts/signals/traces/).
