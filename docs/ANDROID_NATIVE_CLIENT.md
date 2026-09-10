# Stock Day Trader Native Android Client

## Purpose

The native client is a small Android WebView shell for the private Stock Day Trader server. It exists because Chrome will not install the HTTP Tailscale-IP page as a full PWA. The APK can be installed as a normal app while the server remains private inside Tailscale.

## Safety boundary

- The APK contains no NH credentials.
- It has no broker/order implementation.
- It does not change Control v0.8.0.
- It only requests Android INTERNET permission.
- File/content access and WebView debugging are disabled.
- HTTP/HTTPS navigation is restricted to the host configured by the user.
- The configured server URL is stored only in Android app SharedPreferences.
- REAL ORDER remains OFF on the server.

## First launch

1. Turn on Tailscale on the main phone.
2. Open `Stock Day Trader`.
3. Enter the spare phone's private server address, for example `http://100.x.x.x:8000/classic`.
4. The app remembers the address. Use the `주소` button to change it if the spare phone Tailscale IP changes.

## APK build

GitHub Actions workflow `Android Client APK` builds an installable debug APK. Open the workflow run, download artifact `stock-day-trader-android-debug`, extract it, and install `app-debug.apk` on the main phone. Android may require permission to install unknown apps for the browser or file manager used to open the APK.

The debug APK is intended for private personal installation. A future Play Store/release distribution would require a persistent release signing key and a separate release process; this change does not add or store any signing secret.

## SSH remote profile

On a phone with an SSH client, create `~/.ssh/config` with a private profile like:

```sshconfig
Host stockphone
    HostName 100.x.x.x
    User u0_aXXX
    Port 8022
    ServerAliveInterval 30
    ServerAliveCountMax 3
    ConnectTimeout 10
```

Then connect with:

```bash
ssh stockphone
```

Keep the concrete Tailscale IP, Termux username, passwords and private keys out of the public repository. Prefer an SSH key over a stored password when convenient.
