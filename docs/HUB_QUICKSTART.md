# Self-hosted volunteer hub

The hub is a downloadable, self-hosted inference broker. Clients send prompts to its OpenAI-compatible endpoint; approved volunteers connect outbound to lease inference jobs and return fenced streams. Each party keeps its own credentials and model installation. It does not offer a hosted public directory or supply free servers/bandwidth.

Use a separate data directory from the desktop. For a local protocol trial:

```powershell
./swarm-engine-windows-x64.exe hub --data-dir ./hub-state --host 127.0.0.1 --port 8443
```

Remote operation requires a reachable HTTPS origin and valid certificate/private key:

```powershell
./swarm-engine-windows-x64.exe hub --data-dir ./hub-state --host 0.0.0.0 --port 8443 --tls-cert server.pem --tls-key server-key.pem --public-origin https://hub.example:8443
```

On macOS/Linux use `./swarm-engine`, or `swarm-hub` from the Python installation. TLS/firewall/DNS/reverse-proxy configuration belongs to the operator. The CLI prints an operator token. Keep it private; it authorizes administration. `SWARM_OWNER_TOKEN` can explicitly supply that token at startup. No shell/desktop control is sent to volunteers through this protocol.

## Create client access and pair volunteers

The following PowerShell examples are for manual operator use. Substitute your own origin/token; do not publish them:

```powershell
$hubOrigin = 'https://hub.example:8443'
$hubHeaders = @{Authorization='Bearer YOUR_OPERATOR_TOKEN'}
$clientGrant = Invoke-RestMethod -Uri "$hubOrigin/api/hub/clients" -Method Post -Headers $hubHeaders -ContentType 'application/json' -Body '{"name":"Research desktop","rpm":6,"daily_requests":100}'
$invitation = Invoke-RestMethod -Uri "$hubOrigin/api/devices/invite" -Method Post -Headers $hubHeaders
```

Store `clientGrant.api_key` privately now; only its hash remains in the hub database. Give the client this key, not the operator token. Give one volunteer `invitation.token`; it is one-use and lasts five minutes. The volunteer runs:

```powershell
./swarm-engine-windows-x64.exe worker --coordinator https://hub.example:8443 --invite INVITATION --daily-jobs 100 --hours 20-8 --pause-on-battery
```

For a private CA add `--ca-cert ca.pem` supplied and verified by the operator. Every volunteer must install/start Ollama and a fitting model. Ordinary clients need an OS-trusted TLS certificate; use a public certificate for broad distribution.

Inspect arriving devices and approve the intended one:

```powershell
$snapshot = Invoke-RestMethod -Uri "$hubOrigin/api/snapshot" -Headers $hubHeaders
$snapshot.devices
Invoke-RestMethod -Uri "$hubOrigin/api/devices/DEVICE_ID/approve" -Method Post -Headers $hubHeaders
```

Approval gates leasing. Revoke a device with `POST /api/devices/DEVICE_ID/revoke`; revoke a client with `DELETE /api/hub/clients/CLIENT_ID`. Approval, invitation and client-management endpoints require the operator token. Worker and client credentials cannot administer the hub.

## Connect a desktop client

In **Providers > Link provider**, choose **Volunteer hub**, use base URL `https://hub.example:8443/v1`, paste the client key, discover/select a served model, and confirm free eligibility only if the operator supplies it without per-request billing. When launching a mission, explicitly select **Allow prompts and project context on volunteer workers**. Without consent the router excludes public profiles, even if they are assigned manually.

The client API exposes:

- `GET /v1/models`: fitting models currently advertised by approved online workers.
- `POST /v1/chat/completions`: bounded system/user/assistant messages, selected model, optional `stream`, `duplicate_check` and `max_tokens` (1–4096, also capped by the worker's context budget).

Use `Authorization: Bearer CLIENT_KEY`. Streaming returns ordinary text deltas and `[DONE]`; it does not claim native tools, vision or billing token usage. The server permits one active request per client and 64 total, applies each client's RPM/day limits, and returns 429 while the budget/capacity is unavailable. Daily counters use UTC. No eligible volunteer means no inference, not a hidden paid fallback.

Version 0.3 waits up to 30 seconds in a bounded FIFO admission queue and selects idle compatible workers using observed completion/latency samples. A failed or expired job can move once to another compatible worker before any output. Once a stream has emitted text it fails instead of splicing in another answer. Leases fence late replies. Prompt-free receipts record model, status, elapsed time and estimated output size; ordinary job records still contain inference messages.

`duplicate_check: true` needs two distinct available workers and consumes two jobs against RPM/day/mission limits. Both outputs are buffered, compared after whitespace/case normalization and annotated. Select **Request two-worker comparisons** on the desktop community profile to use it. Agreement does not establish truth or prevent collusion. Operators can inspect comparison records; there is no payment/reputation credit system.

## Deployment limits

The operator and volunteers can see prompts, including project text a client chooses to include. The owner keeps provider keys; keys are not contributed to a shared pool. Returned content is untrusted. Observed completion/failure counts help availability scheduling and do not establish identity, answer quality or reputation. This is a controlled pilot for a trusted deployment; a global public network still needs service operations, abuse handling, stronger trust/attestation and independent testing.

Worker availability respects local contribution hours, battery policy and daily accepted-job budgets. Time/battery boundaries prevent new jobs and allow the current one to finish. Whole inference jobs run on one volunteer; this hub does not shard weights. NAT behind the worker needs outbound HTTPS only; the hub still needs inbound HTTPS reachability. No automatic NAT traversal, central registry, leader election or artifact replication is supplied.

Back up both hub databases, preserve the operator configuration and keep TLS private keys separately. A single OS lock owns each hub state directory. Use dedicated deployment accounts/process supervision and manual network configuration appropriate to your host; the app does not install background services automatically.
