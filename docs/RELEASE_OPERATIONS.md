# Release operations

Source licensing is MIT. Runtime/frontend dependency and font notices are generated into `build/notices/` and included in installers/archives. Model licenses remain independent; weights are not bundled.

## Build artifacts

```powershell
.venv/Scripts/python -m pip install -e '.[dev,desktop]'
npm ci
.venv/Scripts/python -m pytest -q
npm run test:ui
.venv/Scripts/python scripts/build_desktop.py
```

Build scripts read the version from `src-tauri/tauri.conf.json`; keep that version, Cargo/Python/npm metadata aligned. Windows produces NSIS, portable and worker ZIPs. macOS/Linux produce their platform bundles and an architecture-specific worker ZIP. The manual `.github/workflows/desktop.yml` matrix uploads artifacts. Tag-triggered runs publish only after the complete matrix passes. Release notes come from `docs/RELEASE_<major>_<minor>.md` when available.

`--bundle-only` regenerates packages from current compiled binaries, useful after a documentation/resource change. `--package-only` rebuilds ZIPs and hashes from existing artifacts. Neither recompiles stale source. On Windows inspect NSIS contents without executing installation:

```powershell
.venv/Scripts/python scripts/check_desktop_packages.py --seven-zip "C:/Program Files/7-Zip/7z.exe" --check-engine
```

This verifies ZIP integrity, binary payloads, MIT/dependency notices and checksums, then tests the extracted headless engine outside the source tree with development runtimes removed from PATH. It opens no desktop windows and sends no input. The workflow additionally runs `scripts/check_windows_install_ci.py` on a disposable GitHub-hosted Windows runner: silent install, installed engine version and silent uninstall. That script refuses a developer desktop. This remote workflow has not been executed here; native dialogs and clean-machine acceptance remain unvalidated.

## Signing and update channel

The native updater requires signed artifacts bound to the manifest's app version (`requireSignedVersion`). Generate a Tauri signing key manually using the upstream [updater instructions](https://v2.tauri.app/plugin/updater/). Secure the private key; never commit it. Configure `TAURI_SIGNING_PRIVATE_KEY` and, if needed, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` in the build environment. The script enables updater artifacts only when the private key is present, and copies adjacent `.sig` files. It does not print secrets. `tauri build` binds signatures to the app version automatically; manual signing must supply `--app-version`. Local 0.3.0 artifacts were built without those credentials.

Updater signing is separate from OS signing. Windows can use an already provisioned certificate through `SWARM_WINDOWS_CERT_THUMBPRINT`; the build uses DigiCert's timestamp endpoint. On the disposable Windows CI runner, optional `SWARM_WINDOWS_PFX_BASE64` and `SWARM_WINDOWS_PFX_PASSWORD` secrets provision a PFX in the current user's certificate store and set its thumbprint. A thumbprint alone cannot provision a certificate on a fresh runner. macOS uses Tauri's `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` setup and requires the corresponding installed certificate/notarization account. Absent secrets are removed rather than treated as signing identities. Linux packaging has its own distribution requirements. No real signing identity has been provided or tested for this local alpha.

Prepare a local manifest from a signed artifact:

```powershell
.venv/Scripts/python scripts/make_update_manifest.py --target windows-x86_64 --base-url https://downloads.example/releases "releases/Swarm Workbench_0.3.0_x64-setup.exe"
```

The tool requires the artifact's adjacent `.sig`, writes `releases/update-manifest.json`, and publishes nothing. Run it against each supported target with the same output file to add platform entries. Use a correctly versioned artifact from the current build. Then manually review/publish the signed files and manifest to HTTPS hosting you control. No hosting, certificates or signing accounts are created by the build.

In **Studio > Backups & updates**, configure the HTTPS manifest and trusted public signing key. **Save and check updates** only checks; **Back up, verify signature, install and restart** explicitly performs installation. There is no startup auto-update or preconfigured public channel. The backend validates the manifest URL; the native updater independently requires HTTPS and signature verification.

## Data migration and rollback

The 0.3 schema uses SQLite `user_version=3`, retains legacy records and adds the new feature records. Uncertain operations, model downloads, tuning jobs and conversions are marked interrupted after restart; model reservations stay conservatively charged. The paired backup format remains version 2, independently of database schema versions. Unknown future database versions are rejected. An OS workspace lock blocks concurrent coordinators/restore. Run backups before deployment; preserve the record and checkpoint databases together.

Create a paired-database backup from Studio, close the app, then restore using:

```powershell
./swarm-engine-windows-x64.exe restore BACKUP.zip --data-dir DATA_DIRECTORY
```

Hashes and SQLite integrity are checked before replacement. Prior databases are retained with `.before-restore-*` suffixes. The pair is replaced sequentially; after an interrupted restore recover from the verified archive or corresponding prior pair before reopening. Vault contents, project files, attachment files, worktrees, TLS private keys and network configuration need their own backups. Session-only keys must be linked again after restart.

## Release acceptance still requiring external validation

Before public production distribution, validate installers on clean machines, manual native dialogs, OS signatures/notarization and signed update/rollback, live providers, real Ollama memory behavior, two physical linked devices, LAN discovery and hosted hub behavior. Validate macOS/Linux packages and any Exo topology on their actual supported hardware. No source-only or mocked test substitutes for those checks.
