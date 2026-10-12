# Swarm Workbench 0.6

From 0.6.4 onward, every mission uses connected models. The offline demonstration mode and its execution controls have been removed from missions, automations, campaigns, comparisons, evaluations, and onboarding. Saved demonstration records remain inspectable and exportable, but cannot resume, branch, or accept new messages. Connect a provider, local model, or paired device to begin.

These twelve additions are part of the downloadable desktop application. Open **Command center** for their controls. The separate Swarm Companion project packages the phone interface as a native Tauri application. No inference or API credentials are hosted at swarms.grafitelab.in.

1. **Mission command center.** Running work, blocked missions, pending agent questions, proposed changes and local/linked capacity have direct links to their existing inspectors and controls. Selecting a project narrows the displayed work. Stop all also disables automatic campaigns.
2. **Project campaigns.** Dependency-checked milestones pin architecture snapshots, share aggregate request/token/time/estimated-spend ceilings and retain all attempts. One milestone runs at a time. Completed work enters acceptance review; rejected attempts can be rerun within the remaining budget. Dependent tasks receive accepted upstream work. Automatic mode advances only after human acceptance. Restart pauses the campaign; resuming an interrupted attempt reuses its existing mission. Public inference cannot be automatically opted into for each milestone.
3. **Feedback-based routing.** Rate a recorded live outcome once, edit the rating and record a correction. The new learned routing policy combines task-specific checks and human scores after three observations. It isolates evidence by an owner-pinned model revision; unknown revisions do not train this policy. Feedback changes route selection, not model weights, and does not automatically send the correction to an agent.
4. **Preflight comparison.** Compare up to ten architectures without inference. Inspect initial stages, full tool-call allowances, conservative tokens, catalog memory-fit estimates, configured routes and warnings. A fresh read-only local inventory accounts for resident models. Account profiles sharing a quota group show its smallest configured request limit, remaining requests, cooldown and observed provider headers; linking more keys does not invent independent quota. Serial timing estimates use matching recorded measurements and otherwise remain unknown. Conditional branches, retries, adaptive expansion, warm-up and external tool charges can change the actual result.
5. **Versioned knowledge graph.** Project facts/decisions/entities/requirements have immutable revision history, source links, dependency revisions and review dates. Changed dependencies, deleted/changed evidence and expiry mark descendants stale. Cycles and cross-project references are rejected. Up to three current records enter agent context with inherited file provenance. This records reviewed knowledge; it does not establish factual truth.
6. **Native phone companion.** A reachable private HTTPS listener issues a ten-minute, one-use QR/JSON invitation. Pairing includes public certificates only, including when the configured PEM file also contains a private key. Larger certificate bundles use JSON export when they cannot fit QR capacity. Review the origin and SHA-256 of the exported PEM certificates before pairing. The phone redeems it for an individual project-scoped viewer/operator/reviewer credential. Watch agents; operators steer/pause/resume and answer questions; reviewers inspect and apply staged changes. Credentials remain in native session memory and need a fresh invitation after the app process exits. Monitoring polls while the app is open; push notifications and background inference are not included. Revoke access in Team & setup.
7. **Live voice.** Explicit microphone capture converts short chunks into 16 kHz mono PCM16 WAV for an owner-installed, trusted local transcription command. Audio temporary files are deleted after each conversion. Edit the transcript before sending it to an individual agent. Spoken replies use local system voices only and have an interrupt button. Microphone/system voice availability still depends on the platform. Transcription binaries and weights are not bundled.
8. **Interactive MCP panels.** Enable MCP Apps on a configured MCP extension, rediscover its tools and grant the relevant tools to a project. Inspect the declared UI resource before opening it. Expiring opaque-origin frames disallow networking, files, forms, nested frames, camera and microphone. The restricted host supports the initialization lifecycle, ping, resizing and reviewed tool interactions; every call retains current project grants, schema checks and a thirty-call panel limit. External-resource-dependent apps may not render with this restrictive policy. Arbitrary links, sampling, model messages and filesystem access are not granted.
9. **Swarm notebooks.** Versioned markdown/Python cells and CSV/JSON/TXT inputs bind to a review hash. Code executes only in an explicitly selected, already installed Docker Python image, pinned to its actual image digest for the run. There is no network, a read-only host mount, CPU/memory/PID/time ceilings and bounded temporary output storage. Inspect captured output/plots/files and export standard .ipynb documents. Docker and a Python image are separate installations; there is no host-code fallback.
10. **Local LoRA workshop.** Select completed live agent examples for a dataset preview. Scripted, incomplete, failed, file-derived and heuristic credential-bearing examples are excluded. Review the content before export/training. The optional CUDA backend reads existing local safetensors weights without remote code or automatic model downloads. It uses the observed CUDA device 0 memory and the installation's VRAM allocation for an estimate. Trained adapters stay candidates. Load a candidate into a compatible external serving runtime, pin the profile to the displayed adapter revision and pass a live project evaluation using that exact profile/revision before promotion. Promoted profiles are preferred only among already admitted automatic routes; rollback restores the prior selection. Runtime revision declarations cannot attest which weights an external server actually loaded. Training allocations are estimates, not hard GPU limits.
11. **Power scheduling.** Optional driver sampling measures supported NVIDIA GPU board power and temperature. Integrate only consecutive measurements with gaps at most fifteen seconds. Inspect sampled seconds beside daily UTC Wh. Temperature/energy ceilings, quiet hours with an explicit UTC offset and optional fresh-telemetry requirements control admission of new local/private device requests. Calls already running finish normally. CPU/wall power and unobserved time are excluded. A low-power preference uses measured device watts, not an estimate of energy per task; cloud power remains unknown.
12. **Compatibility packs.** Explicit live routes run structured-output, file-tool, handoff and reconstruction workflows against an isolated fixture project. The recovery case actually closes/rebuilds Engine and its SQLite saver, resumes the saved graph, and verifies that its completed source stage was not called again. Export contains declared model/runtime/app/platform versions, durations, request counts and check results, without prompt/credential content. Import is explicitly unverified community evidence; imports do not enable routes or grant capabilities. Tests of this harness use scripted provider fixtures and do not validate any real model's accuracy.

## Local training environment

Use a separate Python environment with a CUDA-compatible PyTorch installation and the optional training extra:

```text
python -m venv training-env
training-env/Scripts/python -m pip install ".[training]"
```

Run these commands from a downloaded or cloned Swarm Workbench source checkout. On macOS/Linux use `training-env/bin/python`. This optional environment is intentionally separate from the frozen desktop engine. Select its Python executable and the downloaded model directory in the workshop. Review the model's license and the selected examples when redistributing an adapter. A live holdout evaluation should use tasks not present in its training examples.

## Local voice configuration

An example owner-installed whisper.cpp argument array is:

```json
["C:/tools/whisper/whisper-cli.exe", "-m", "C:/models/ggml-base.en.bin", "-f", "{input}", "-otxt", "-of", "{output}"]
```

Select real executable/model paths for each installation. Trusting this command permits that program to run with the user's OS access. Do not configure a command that opens another application or takes over desktop input.

## Mobile builds

See [the companion guide](COMPANION.md). CI produces a development-signed Android APK and an unsigned iOS simulator build. A physical iOS distribution requires the publisher's Apple team/certificates/provisioning and is separate from simulator compilation. Store publishing and production OS signing are not implied by a successful source build.

## Primary references

- [MCP Apps stable specification](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx).
- [Tauri mobile barcode scanner](https://v2.tauri.app/plugin/barcode-scanner/) and [mobile development](https://v2.tauri.app/develop/).
- [whisper.cpp microphone example](https://github.com/ggml-org/whisper.cpp/blob/master/examples/stream/README.md).
- [PEFT quantized LoRA training](https://huggingface.co/docs/peft/developer_guides/quantization), [TRL SFTTrainer](https://huggingface.co/docs/trl/sft_trainer).
- [NVIDIA NVML API](https://docs.nvidia.com/deploy/pdf/NVML_API_Reference_Guide.pdf).
