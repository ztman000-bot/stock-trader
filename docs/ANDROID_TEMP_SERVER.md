# Temporary Android Phone Server (Termux)

This profile is for development/research/Paper operation while the Windows laptop is paused. It is **not** a production live-order deployment profile.

## Safety rules

- Keep `APP_MODE=paper`.
- Keep `ENABLE_TRADING=false`.
- Existing protected holding `068270` remains protected by application logic.
- Never commit or upload `server/.env`.
- Run only one server device at a time. Stop the Windows server before the Android server starts collecting data.

## Recommended Android setup

Use a spare Android phone with stable Wi-Fi, charger, battery protection if supported, and Tailscale. Disable battery optimization for Termux and Tailscale. For unattended restart, Termux:Boot can be added later after the manual setup is confirmed stable.

## 1. Install tools on the phone

Install Termux from a maintained Termux distribution, then in Termux:

```sh
pkg update
pkg upgrade
pkg install python git openssh termux-api
python --version
```

Python 3.10+ is required by the pinned NH PLUG SDK version.

## 2. Clone the project

```sh
cd ~
git clone https://github.com/ztman000-bot/stock-trader.git
cd stock-trader/server
python -m pip install --upgrade pip
pip install -r requirements-android.txt
```

`requirements-android.txt` intentionally uses plain `uvicorn` instead of `uvicorn[standard]` so optional native extensions are not required on Android.

## 3. Create the phone-local `.env`

Create `~/stock-trader/server/.env` locally. Do not send credentials through chat and do not commit the file.

At minimum preserve the same NH PLUG App Key/App Secret/base URL settings used on Windows and explicitly keep:

```env
APP_MODE=paper
ENABLE_TRADING=false
```

## 4. Preserve accumulated research data (optional but recommended)

Before copying the database, stop the Windows Stock Trader server completely so SQLite WAL writes have stopped. Copy:

```text
Windows: C:\Users\jang0\stock-trader\server\market_data.db
Android: ~/stock-trader/server/market_data.db
```

Do not copy `.env` through GitHub. Use USB/local transfer or recreate it locally on the phone.

If the database is not copied, the phone will start a new research history and old validation samples will remain only on the laptop.

## 5. Start manually first

```sh
cd ~/stock-trader/server
bash start_android.sh
```

The launcher forces `ENABLE_TRADING=false`, applies a lighter phone profile, takes a Termux wake lock, and starts the unified app on port 8000.

On the server phone itself:

```text
http://127.0.0.1:8000/classic
```

From another device on the same Tailscale tailnet, use the spare phone's Tailscale IP:

```text
http://100.x.x.x:8000/classic
```

The browser connection is HTTP, but Tailscale traffic is carried inside the encrypted tailnet. Do not expose port 8000 through the home router or a public port-forward.

## 6. Android settings for stability

- Termux battery optimization: Unrestricted / Don't optimize.
- Tailscale battery optimization: Unrestricted / Don't optimize.
- Keep Wi-Fi on during sleep.
- Keep the phone on external power; use battery protection/80% charge limit if the device supports it.
- Avoid Samsung/Android "deep sleeping apps" for Termux and Tailscale.
- Do not use task killers or memory-cleaner apps.

## 7. Boot automation only after manual verification

After one or two days of stable manual operation, install Termux:Boot and create `~/.termux/boot/stock-trader`:

```sh
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
cd "$HOME/stock-trader/server"
nohup bash start_android.sh >> "$HOME/stock-trader/android-server.log" 2>&1 &
```

Then make it executable:

```sh
chmod +x ~/.termux/boot/stock-trader
```

## 8. Update procedure on Android

Do not use the Windows `remote_update.cmd` on Android. In Termux:

```sh
cd ~/stock-trader
git pull --ff-only
cd server
pip install -r requirements-android.txt
```

Restart `start_android.sh` after an update when server code changed.

## 9. Returning to the laptop later

Stop the Android server first. If the phone accumulated research data you want to keep, copy `market_data.db` back to the laptop while both servers are stopped. Then restart only the laptop server. Never run both collectors against the same research role at the same time because duplicate snapshots and API load can distort validation.

## Temporary-server limitations

Android is suitable for interim research/Paper collection, but it is less predictable than Windows/Linux mini-PC for unattended 24/7 operation because the OS may reclaim background processes, Wi-Fi may sleep, and thermal/battery policies vary by model. Keep real orders disabled until the project has passed the full validation and a stable server platform is selected.
