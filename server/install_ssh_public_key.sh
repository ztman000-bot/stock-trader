#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

HOME=/data/data/com.termux/files/home
SSH_DIR="$HOME/.ssh"
AUTH="$SSH_DIR/authorized_keys"

usage(){
  echo "Usage: bash install_ssh_public_key.sh /path/to/public_key.pub"
  echo "   or: bash install_ssh_public_key.sh 'ssh-ed25519 AAAA... comment'"
  echo "Public key only. Never pass a private key file."
}

[ "$#" -ge 1 ] || { usage; exit 2; }
SOURCE="$1"
if [ -f "$SOURCE" ]; then
  KEY=$(grep -m1 -E '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-)' "$SOURCE" 2>/dev/null || true)
else
  KEY="$SOURCE"
fi

case "$KEY" in
  ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*\ *|sk-ssh-ed25519@openssh.com\ *|sk-ecdsa-sha2-*\ *) ;;
  *) echo '[ERROR] valid OpenSSH public key not found'; usage; exit 3 ;;
esac

mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"
touch "$AUTH"
chmod 600 "$AUTH"

if grep -Fxq "$KEY" "$AUTH"; then
  echo '[OK] public key already registered'
else
  printf '%s\n' "$KEY" >> "$AUTH"
  echo '[OK] public key registered for passwordless SSH'
fi

if command -v sshd >/dev/null 2>&1; then
  pgrep -x sshd >/dev/null 2>&1 || sshd || true
fi

echo "[INFO] Test key login from the main phone before changing or disabling password authentication."
