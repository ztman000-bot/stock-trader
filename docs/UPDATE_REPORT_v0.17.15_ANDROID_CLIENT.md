# v0.17.15 UX Hotfix — Native Android Client

## Goal

Provide a normal installable Android app for the private Tailscale-hosted Stock Day Trader UI, without requiring Chrome PWA installation over HTTPS.

## Added

- `android-client/`: minimal native Android WebView shell
- first-launch private server URL setup and persistent local preference
- in-app `주소` and `새로고침` controls
- same-configured-host navigation/resource restriction
- file/content access disabled and WebView debugging disabled
- connection state message for Tailscale/server troubleshooting
- GitHub Actions `Android Client APK` build and downloadable debug APK artifact
- native client safety tests and operator documentation

## Distribution model

The CI artifact is a debug-signed APK intended for private installation on the user's main phone. No signing key or other credential is committed. If formal release distribution is needed later, add a separate protected signing workflow.

## Remote administration

The recommended remote shell path remains Tailscale + Termux OpenSSH. Concrete private device IP/user details are intentionally not committed to the public repository; use a local `~/.ssh/config` profile and connect by alias.

## Safety

- Control v0.8.0 unchanged
- `ENABLE_TRADING=False` unchanged
- REAL ORDER OFF unchanged
- no server entry/exit/sizing/daily-lock semantics changed
- no NH credential embedded in the APK
- no broker/order API implemented in the APK
