"""Optional encrypted off-device database backup.

Disabled by default. When explicitly enabled, this module creates a normal
WAL-safe SQLite snapshot with db_backup.snapshot(), encrypts only that snapshot
with OpenSSL AES-256-CBC/PBKDF2, and uploads the ciphertext with HTTPS PUT.
Credentials/passphrases are read from the phone's .env or an external secret
file and are never written to Git, logs, or status output.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / '.env')
except Exception:
    pass

from db_backup import snapshot as db_snapshot

VERSION = '0.17.12'
KST = ZoneInfo('Asia/Seoul')
HOME = Path.home()
STATE_FILE = HOME / '.stock-trader-offsite-backup-state.json'
ENABLED = (os.getenv('OFFSITE_BACKUP_ENABLED') or 'false').strip().lower() in {'1', 'true', 'yes', 'on'}
PUT_URL = (os.getenv('OFFSITE_BACKUP_PUT_URL') or '').strip()
BEARER_TOKEN = (os.getenv('OFFSITE_BACKUP_BEARER_TOKEN') or '').strip()
PASSPHRASE_FILE = (os.getenv('OFFSITE_BACKUP_PASSPHRASE_FILE') or '').strip()
PBKDF2_ITER = max(100_000, min(int(os.getenv('OFFSITE_BACKUP_PBKDF2_ITER', '200000')), 2_000_000))
HTTP_TIMEOUT_SEC = max(10, min(int(os.getenv('OFFSITE_BACKUP_HTTP_TIMEOUT_SEC', '60')), 300))
DAILY_AFTER_MINUTE = max(15 * 60 + 40, min(int(os.getenv('OFFSITE_BACKUP_AFTER_MINUTE', str(16 * 60))), 23 * 60 + 59))
DAEMON_INTERVAL_SEC = max(300, int(os.getenv('OFFSITE_BACKUP_DAEMON_SEC', '900')))
UPLOAD_CHUNK_BYTES = max(64 * 1024, min(int(os.getenv('OFFSITE_BACKUP_CHUNK_BYTES', str(1024 * 1024))), 8 * 1024 * 1024))


def _write_private(path: Path, data: dict):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _load_state():
    try:
        data = json.loads(STATE_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_result(result: dict):
    old = _load_state()
    keep = {
        'componentVersion': VERSION,
        'enabled': ENABLED,
        'configured': configuration_status()['configured'],
        'lastAttemptAt': datetime.now(KST).isoformat(timespec='seconds'),
        'lastOk': bool(result.get('ok')),
        'lastError': result.get('error'),
        'lastCipherSha256': result.get('cipherSha256'),
        'lastBytes': result.get('bytes'),
    }
    if result.get('ok'):
        keep['lastSuccessAt'] = datetime.now(KST).isoformat(timespec='seconds')
    elif old.get('lastSuccessAt'):
        keep['lastSuccessAt'] = old.get('lastSuccessAt')
    _write_private(STATE_FILE, keep)


def _passphrase():
    direct = os.getenv('OFFSITE_BACKUP_PASSPHRASE') or ''
    if direct:
        return direct
    if PASSPHRASE_FILE:
        p = Path(PASSPHRASE_FILE).expanduser()
        try:
            value = p.read_text(encoding='utf-8').strip()
            if value:
                return value
        except Exception:
            return ''
    return ''


def _valid_https_target(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
        return bool(
            parsed.scheme.lower() == 'https'
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
        )
    except Exception:
        return False


def configuration_status():
    https = _valid_https_target(PUT_URL)
    openssl = bool(shutil.which('openssl'))
    passphrase_ready = bool(_passphrase())
    return {
        'enabled': ENABLED,
        'configured': bool(ENABLED and https and openssl and passphrase_ready),
        'httpsTarget': https,
        'opensslAvailable': openssl,
        'passphraseConfigured': passphrase_ready,
        'bearerConfigured': bool(BEARER_TOKEN),
    }


def _target_url(filename: str):
    if not _valid_https_target(PUT_URL):
        raise ValueError('OFFSITE_BACKUP_PUT_URL must be an https:// URL without embedded credentials')
    encoded = urllib.parse.quote(filename, safe='')
    return PUT_URL.replace('{filename}', encoded) if '{filename}' in PUT_URL else PUT_URL


def _encrypt_snapshot(src: Path):
    openssl = shutil.which('openssl')
    secret = _passphrase()
    if not openssl:
        raise RuntimeError('openssl command is unavailable')
    if not secret:
        raise RuntimeError('offsite backup passphrase is not configured')
    fd, tmp_name = tempfile.mkstemp(prefix='stock-trader-', suffix='.db.enc', dir=str(src.parent))
    os.close(fd)
    dest = Path(tmp_name)
    env = os.environ.copy()
    env['STOCK_TRADER_OFFSITE_PASSPHRASE'] = secret
    try:
        subprocess.run([
            openssl, 'enc', '-aes-256-cbc', '-salt', '-pbkdf2', '-iter', str(PBKDF2_ITER),
            '-md', 'sha256', '-in', str(src), '-out', str(dest),
            '-pass', 'env:STOCK_TRADER_OFFSITE_PASSPHRASE',
        ], check=True, timeout=300, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return dest
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    finally:
        env.pop('STOCK_TRADER_OFFSITE_PASSPHRASE', None)


def _upload(cipher: Path):
    """Stream ciphertext over TLS without loading the database into RAM."""
    url = _target_url(cipher.name)
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname
    if not host:
        raise ValueError('offsite backup host is missing')
    port = parsed.port or 443
    target = urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
    size = cipher.stat().st_size
    digest = hashlib.sha256()
    headers = {
        'Content-Type': 'application/octet-stream',
        'Content-Length': str(size),
        'X-Stock-Trader-Filename': cipher.name,
        'User-Agent': f'stock-trader-offsite-backup/{VERSION}',
    }
    if BEARER_TOKEN:
        headers['Authorization'] = f'Bearer {BEARER_TOKEN}'

    conn = http.client.HTTPSConnection(host, port=port, timeout=HTTP_TIMEOUT_SEC)
    try:
        conn.putrequest('PUT', target, skip_accept_encoding=True)
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders()
        with cipher.open('rb') as fh:
            while True:
                chunk = fh.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                conn.send(chunk)
        response = conn.getresponse()
        status = int(response.status)
        response.read(65536)
        if status >= 400:
            raise RuntimeError(f'backup upload HTTP {status}')
    finally:
        conn.close()
    return size, digest.hexdigest()


def run_once():
    if not ENABLED:
        return {'ok': False, 'disabled': True, 'error': 'OFFSITE_BACKUP_ENABLED is false'}
    cfg = configuration_status()
    if not cfg['configured']:
        result = {'ok': False, 'error': 'offsite backup configuration incomplete', 'configuration': cfg}
        _save_result(result)
        return result
    snap = db_snapshot('offsite-encrypted')
    if not snap.get('ok'):
        result = {'ok': False, 'error': f"local snapshot failed: {snap.get('error') or 'unknown'}"}
        _save_result(result)
        return result
    src = Path(snap['path'])
    cipher = None
    try:
        cipher = _encrypt_snapshot(src)
        size, digest = _upload(cipher)
        result = {
            'ok': True,
            'encrypted': True,
            'cipher': 'AES-256-CBC-PBKDF2-SHA256',
            'pbkdf2Iterations': PBKDF2_ITER,
            'bytes': size,
            'cipherSha256': digest,
            'localSnapshot': src.name,
        }
    except Exception as exc:
        result = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'[:500]}
    finally:
        if cipher is not None:
            cipher.unlink(missing_ok=True)
    _save_result(result)
    return result


def _success_today(now):
    value = _load_state().get('lastSuccessAt')
    if not value:
        return False
    try:
        return datetime.fromisoformat(str(value)).astimezone(KST).date() == now.date()
    except Exception:
        return False


def run_daemon():
    if not ENABLED:
        return 0
    while True:
        now = datetime.now(KST)
        minute = now.hour * 60 + now.minute
        if now.weekday() < 5 and minute >= DAILY_AFTER_MINUTE and not _success_today(now):
            run_once()
        time.sleep(DAEMON_INTERVAL_SEC)


def status():
    state = _load_state()
    out = {'componentVersion': VERSION, **configuration_status()}
    for key in ('lastAttemptAt', 'lastSuccessAt', 'lastOk', 'lastError', 'lastCipherSha256', 'lastBytes'):
        if key in state:
            out[key] = state[key]
    out['secretsExposed'] = False
    out['orderAccess'] = False
    return out


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--once', action='store_true')
    p.add_argument('--status', action='store_true')
    p.add_argument('--daemon', action='store_true')
    p.add_argument('--instance-version', default=None)
    args = p.parse_args(argv)
    if args.instance_version and args.instance_version != VERSION:
        print(f'component version mismatch: requested={args.instance_version} actual={VERSION}')
        return 2
    if args.daemon:
        return run_daemon()
    result = run_once() if args.once else status()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('ok', True) or result.get('disabled') else 1


if __name__ == '__main__':
    raise SystemExit(main())
