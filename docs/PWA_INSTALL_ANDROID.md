# Chrome/PWA install on Android

Stock Day Trader Classic is a Progressive Web App shell. The install UI is display-only and never changes Paper/Control state.

## Secure-context requirement

Chrome can install a full PWA only from a secure context. `http://127.0.0.1` is allowed for local development, but a remote Tailscale IPv4 URL such as `http://100.x.x.x:8000/classic` is not a secure context. In that case Chrome may only offer a normal Home-screen shortcut and the Service Worker install flow is unavailable.

For a real Chrome-installed app, expose the same Classic UI through a valid HTTPS origin inside the tailnet (for example Tailscale Serve/HTTPS with a valid tailnet certificate), then open `/classic` from that HTTPS origin. Do not expose the trading bridge to the public Internet merely to obtain HTTPS.

## Expected user flow on a valid HTTPS origin

1. Open `/classic` in Chrome on Android.
2. The UI shows an `앱 설치` button when Chrome fires `beforeinstallprompt`.
3. Tap it and accept Chrome's install prompt.
4. After installation the UI opens in standalone mode without the normal browser chrome.

If the app is already installed, the button shows `앱 설치됨`. If the origin is HTTP/non-secure, the UI clearly reports that HTTPS is required instead of pretending the app is installable.

## Safety

The manifest, install prompt, Service Worker and local display cache are UI-only. They do not send orders, change Control v0.8.0, alter Paper entry/exit rules, or enable real trading.
