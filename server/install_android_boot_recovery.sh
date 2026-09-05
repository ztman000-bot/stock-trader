#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

HOME=/data/data/com.termux/files/home
ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
BOOT_DIR="$HOME/.termux/boot"
BOOT_FILE="$BOOT_DIR/stock-trader"
BOOT_LOG="$HOME/stock-trader-boot.log"

for f in "$SERVER/recover_android_server.sh" "$SERVER/android_watchdog_v2.sh"; do
  if [ ! -f "$f" ]; then
    echo "[ERROR] required file missing: $f"
    exit 10
  fi
done

mkdir -p "$BOOT_DIR"
chmod 700 "$BOOT_DIR"
chmod +x "$SERVER/recover_android_server.sh" "$SERVER/android_watchdog_v2.sh" "$SERVER/start_android.sh" 2>/dev/null || true

cat > "$BOOT_FILE" <<'BOOT'
#!/data/data/com.termux/files/usr/bin/bash
set -u
HOME=/data/data/com.termux/files/home
ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
LOG="$HOME/stock-trader-boot.log"

command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock >/dev/null 2>&1 || true

# SSH remote administration. Do not expose router ports; use the Tailscale address only.
if command -v sshd >/dev/null 2>&1; then
  pgrep -x sshd >/dev/null 2>&1 || sshd >> "$LOG" 2>&1 || true
fi

# Give Android/Tailscale/network a short time to settle, then repair/start the server and watchdog.
(
  sleep 15
  cd "$SERVER" || exit 1
  bash "$SERVER/recover_android_server.sh" >> "$LOG" 2>&1 || true
  if ! pgrep -f 'android_watchdog_v2.sh' >/dev/null 2>&1; then
    nohup bash "$SERVER/android_watchdog_v2.sh" >> "$HOME/stock-trader-watchdog.log" 2>&1 </dev/null &
  fi
) >> "$LOG" 2>&1 </dev/null &

exit 0
BOOT

chmod 700 "$BOOT_FILE"

echo "[OK] Termux:Boot launcher installed: $BOOT_FILE"
echo "[OK] It will start sshd, repair/start Stock Trader, then ensure watchdog v2 is running."
echo "[INFO] Install/open the Termux:Boot app once, then reboot-test when convenient."
echo "[INFO] Keep Termux, Termux:Boot and Tailscale battery setting = Unrestricted/Not optimized."
echo "[INFO] Boot log: $BOOT_LOG"
