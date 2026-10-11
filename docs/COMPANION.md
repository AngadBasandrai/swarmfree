# Swarm Companion

The companion is a downloadable native application in `companion/`, built with Tauri 2 for Android/iOS. It has its own Rust HTTPS client and scoped credential store in session memory. It does not bundle or start the desktop Python engine.

## Pairing

1. On the desktop, enable a reachable private HTTPS listener in Devices. Configure its certificate with the correct DNS/IP subject alternative names and restart the desktop yourself.
2. In Command center, select a project, open Phone companion and choose a role. Viewer watches; operator watches, steers and answers; reviewer watches and applies reviewed changes.
3. Create an invitation. Scan its QR in Swarm Companion, import its JSON file, or paste the invitation JSON.
4. Inspect the HTTPS origin and PEM certificate SHA-256. Verify these against the owner's desktop display, then pair. TLS hostname and certificate validation remain enabled.
5. Watch missions and individual agents. Access is restricted to the selected projects and role. Disconnect clears the native credential. Closing the app process clears it too; pair with a fresh invitation next time.
6. Revoke the member under Team & setup when finished. Revocation stops future phone actions and pauses missions created by that team identity.

Both devices need a route to the private listener. This does not operate a hosted relay or configure anyone's firewall. Provider credentials never go to the phone.

## Builds

Install Node, Rust and the [Tauri mobile prerequisites](https://v2.tauri.app/start/prerequisites/), then:

```text
cd companion
npm ci
npm run build
npm run tauri -- android init --ci
npm run tauri -- android build --debug --target aarch64 --apk --ci
```

On a Mac with Xcode, build a simulator package:

```text
npm run tauri -- ios init --ci
npm run tauri -- ios build --debug --target aarch64-sim --no-sign --ci
```

The Mobile companion GitHub workflow performs both builds and uploads development artifacts. The Android APK is for sideload testing and uses a development signing identity. Production signing, Play/App Store submission and a physical iOS IPA require publisher credentials. No signing identities are invented or embedded.

Desktop development only checks the Rust client and request boundary; it does not launch the companion, an emulator or a browser window. Physical phone pairing and camera permission remain separate acceptance checks.
