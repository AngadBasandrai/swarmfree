# Swarm worker and server

The Windows ZIP bundles the engine runtime, MIT license, dependency notices and these guides. End users do not need Python or Node.js. On other platforms use the corresponding engine binary or the Python source package. Replace `swarm-engine-windows-x64.exe` below with `./swarm-engine` on macOS/Linux.

## Pair a private device

1. Install/start Ollama on the contributing computer. Download a model that fits its own RAM/VRAM allocation.
2. In the owner desktop's **Devices > Connection settings**, set a reachable HTTPS origin and port. Generate a certificate for that hostname/IP or select existing PEM certificate/key files. Save and restart the desktop.
3. Allow the listener port on the owner's firewall/private network. This is a manual network configuration step.
4. Choose **Pair a device > Create certificate-bearing pairing bundle**. Transfer the bundle privately. It expires after five minutes and contains a one-use invitation plus the public TLS trust certificate.
5. On the worker computer, run:

```powershell
./swarm-engine-windows-x64.exe worker --pair BUNDLE --name "Second computer" --ram-gb 12 --vram-gb 4 --context 4096 --daily-jobs 100 --pause-on-battery
```

Approve the arriving device in the desktop. It serves one leased inference job at a time and renews its lease during loading/generation. Prompt streams are accepted only under the current lease. Revocation removes authority and cancels outstanding jobs.

To use an ordinary invitation and an existing certificate instead:

```powershell
./swarm-engine-windows-x64.exe worker --coordinator https://owner.example:8443 --invite INVITATION --name "Second computer" --ca-cert ca.pem
```

Omit `--ca-cert` for an OS-trusted public certificate. HTTP beyond loopback is rejected. Pair only with a trusted origin/bundle; the supplied certificate establishes trust in that owner, not an independent identity check.

The worker keeps its credential in the OS vault and can reconnect later using `--coordinator` without `--invite`. If no usable OS vault is available, explicitly add `--ephemeral`; repeat pairing after restarting. `--data-dir` selects its independent state directory.

## Contribution limits

- `--ram-gb`, `--vram-gb`, `--context`: local model admission budgets; these are estimates, not hard OS memory limits. Each device detects its own hardware.
- `--daily-jobs 100`: maximum accepted jobs per UTC day, persisted across restarts for that coordinator. Failed jobs count too.
- `--hours 20-8`: local contribution hours, including windows across midnight. Omit for all hours.
- `--pause-on-battery`: stop accepting new work while battery power is detected. An already leased generation is allowed to finish.

The worker advertises no models when its time/day/battery policy prevents new work. It still sends heartbeats. Models must already be installed on the contributing machine. Inference jobs contain model IDs and messages; provider keys and owner tokens stay at the coordinator. Any file text included in messages is visible to the worker.

## Optional private execution (0.4)

To contribute isolated project checks, explicitly add `--execution-image YOUR_INSTALLED_IMAGE` to the worker command. Docker and the chosen image must already be installed; images are never pulled automatically. The coordinator must approve execution for that paired device under **Control room > Execution tools > Execution workers**, then grant the device to a project. Give the requesting agent the `remote.execute` tool and configure the named check's argument array.

Execution jobs transfer a bounded snapshot of eligible UTF-8 project files to an ephemeral container. Containers have no network, a read-only filesystem outside their temporary project mount, and CPU/memory/PID limits. Returned changes are staged for review in the desktop; they do not apply themselves. Public hub workers cannot execute private projects. See [the 0.4 guide](FEATURES_0_4.md) for transfer limits, grants, cancellation and trust boundaries.

## Outbound encrypted device relay

Version 0.3 supports private device pairing through a separately hosted HTTPS relay when the owner's computer is not reachable inbound. Start the relay on a reachable host, then enter its origin and operator provisioning credential in the owner's **Control room > Internet links & workers**. Transfer the private QR/bundle to the worker and run the same `worker --pair BUNDLE` command. Approve that worker in Devices.

```powershell
./swarm-engine-windows-x64.exe relay --data-dir relay-state --host 0.0.0.0 --tls-cert server.pem --tls-key server-key.pem
./swarm-engine-windows-x64.exe worker --coordinator https://relay.example:9443 --relay-channel CHANNEL_ID
```

The second command reconnects a previously paired worker using its stored vault credentials; it does not create a channel. Each channel expires after 30 days. Session-only credentials need new pairing after restart. Pairing bundles include the shared encryption key: keep them private. The relay sees connection/message metadata and retains bounded ciphertext for at most two minutes. It handles inference protocol only. No relay is hosted by this application. See [FEATURES_0_3.md](FEATURES_0_3.md) for trust boundaries and reconnection limits.

## Volunteer hub

The same worker can connect outbound to a reachable self-hosted hub. Obtain a one-use invitation from that hub's operator, pair, then wait for operator approval. The contributing computer needs no inbound listener. The hub needs HTTPS reachability; it is not automatically hosted by this app. See [HUB_QUICKSTART.md](HUB_QUICKSTART.md).

## Other bundled commands

```powershell
./swarm-engine-windows-x64.exe coordinator --host 0.0.0.0 --port 8000 --tls-cert server.pem --tls-key server-key.pem --public-origin https://coordinator.example:8000
./swarm-engine-windows-x64.exe hub --data-dir hub-state --host 0.0.0.0 --port 8443 --tls-cert server.pem --tls-key server-key.pem --public-origin https://hub.example:8443
./swarm-engine-windows-x64.exe restore BACKUP.zip --data-dir WORKBENCH_DATA_DIRECTORY
```

Close the application before restore. The restore validates hashes/database integrity, keeps prior databases and excludes project files/credential changes. Each state directory permits one active coordinator. Whole requests move between devices; model weights and their VRAM are not combined by this worker.
