# Public hosting: swarmfree

Source and installers: https://github.com/AngadBasandrai/swarmfree

Registry: `https://swarms.grafitelab.in/registry/index.json`

Updater: `https://swarms.grafitelab.in/updates/stable.json`

Fallback registry: `https://angadbasandrai.github.io/swarmfree/registry/index.json`. The default desktop update check also tries the GitHub Releases feed. Owner-entered alternative Studio endpoints use that configured server and public key.

The hosted files are static JSON and signed package declarations. The downloadable desktop application runs its engine locally; Pages does not run inference or hold users' API keys.

## DNS connection

Enable GitHub Pages with GitHub Actions as the source, and configure its custom domain as `swarms.grafitelab.in` before creating DNS. In grafitelab.in's DNS zone add:

| Type | Name | Target |
| --- | --- | --- |
| CNAME | swarms | angadbasandrai.github.io |

The CNAME target excludes the repository name. Verify the domain in the GitHub account settings if desired; GitHub supplies that account-specific TXT record. Once DNS validates and a certificate is ready, enable HTTPS enforcement. DNS and certificate issuance may take time. [GitHub's domain instructions](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site).

## Publisher credentials and release

`python scripts/prepare_public_release.py` creates or reuses protected Ed25519 registry and Tauri updater identities, writes public pins into source, and signs example packages. It requires a persistent OS credential vault. Private updater material is encrypted in the ignored `build/publisher-private/` directory and preserved in the vault. Preserve the vault entries and encrypted updater material to avoid losing the identity installed clients trust.

`python scripts/prepare_public_release.py --github-secrets` provisions `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` and `SWARM_REGISTRY_PRIVATE_KEY` through stdin on the named repository. Never commit/export secret material with the source. The release workflow accepts optional Windows signing and Apple notarization credentials separately.

Push an annotated `vX.Y.Z` tag after bumping Python/npm/Cargo/Tauri versions, or dispatch the Desktop artifacts workflow for a build without publication. Tagged release jobs test and build in clean runners, merge signed artifact platform entries into `stable.json`, upload GitHub Release assets, and update the `release-channel` branch. The Pages workflow publishes `public/` from that branch (or main before a release exists). It only advances the channel after all required platforms succeed.

The signed index expires after one year. Re-run the publisher preparation and commit regenerated public metadata before expiry; a refreshed index should have a larger serial. A CI-produced updated index can use the protected registry signing secret through `scripts/sign_registry_ci.py`.

No client automatically installs updates. The owner reviews an offered version and requests installation. Tauri update signatures authenticate the publisher; they are independent of Authenticode/macOS signing. [Tauri updater instructions](https://v2.tauri.app/plugin/updater/).
