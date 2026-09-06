"""Independent Android remote-health beacon.

Runs outside FastAPI so it can distinguish a dead Stock Trader server from a
phone/network outage. It never sends account data, symbols, positions, PnL,
credentials, or order information. Default transport is a random private-ish
ntfy topic stored only on the Android device. Anyone who knows that topic URL
can read it, so treat the URL like a password.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / '.env')
except Exception:
    pass

VERSION = '0.17.11'
HOME = Path.home()
TOPIC_FILE = HOME / '.stock-trader-remote-health-topic'
STATE_FILE = HOME / '.stock-trader-remote-health-state.json'
UPDATE_FLAG = HOME / '.stock-trader-update-in-progress'
LOCAL_HEALTH = 'http://127.0.0.1:8000/api/health'
LOCAL_RUNTIME = 'http://127.0.0.1:8000/api/system/runtime-health'
LOCAL_UPDATE = 'http://127.0.0.1:8000/api/system/update/status'
DEFAULT_BASE_URL = 'https://ntfy.sh'
CHECK_INTERVAL_SEC = max(15, int(os.getenv('REMOTE_HEALTH_CHECK_SEC', '30')))
HEARTBEAT_INTERVAL_SEC = max(600, int(os.getenv('REMOTE_HEALTH_HEARTBEAT_SEC', '600')))
MIN_CHANGE_PUBLISH_SEC = max(30, int(os.getenv('REMOTE_HEALTH_CHANGE_MIN_SEC', '60')))
HTTP_TIMEOUT_SEC = max(2, min(int(os.getenv('REMOTE_HEALTH_HTTP_TIMEOUT_SEC', '6')), 20))
DEVICE_NAME = (os.getenv('REMOTE_HEALTH_DEVICE') or 'android-stock-trader').strip()[:80]
ENABLED = (os.getenv('REMOTE_HEALTH_ENABLED') or 'true').strip().lower() not in {'0', 'false', 'no', 'off'}


def _utc_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _write_private(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def topic():
    configured = (os.getenv('REMOTE_HEALTH_TOPIC') or '').strip()
    if configured:
        return configured
    try:
        existing = TOPIC_FILE.read_text(encoding='utf-8').strip()
        if len(existing) >= 24:
            return existing
    except Exception:
        pass
    value = 'stocktrader-' + secrets.token_hex(24)
    _write_private(TOPIC_FILE, value + '\n')
    return value


def base_url():
    return (os.getenv('REMOTE_HEALTH_BASE_URL') or DEFAULT_BASE_URL).strip().rstrip('/')


def status_url():
    custom = (os.getenv('REMOTE_HEALTH_STATUS_URL') or '').strip()
    if custom:
        return custom
    return f'{base_url()}/{topic()}/json?poll=1&since=12h'


def _get_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': f'stock-trader-remote-health/{VERSION}'})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as r:
        raw = r.read(262144).decode('utf-8', 'replace')
        if int(getattr(r, 'status', 200)) >= 400:
            raise RuntimeError(f'HTTP {r.status}')
        return json.loads(raw)


def collect_local_status():
    now = _utc_iso()
    update_active = UPDATE_FLAG.exists()
    try:
        health = _get_json(LOCAL_HEALTH)
    except Exception as exc:
        return {
            'device': DEVICE_NAME,
            'state': 'UPDATING' if update_active else 'SERVER_DOWN',
            'server': False,
            'paper': None,
            'realOrder': False,
            'quotesFresh': None,
            'marketSession': None,
            'version': None,
            'timestamp': now,
            'detail': 'planned update' if update_active else f'health unavailable: {type(exc).__name__}',
        }

    paper = str(health.get('mode') or '').lower() == 'paper'
    real_order = bool(health.get('tradingEnabled'))
    market_session = bool((health.get('collector') or {}).get('marketSession'))
    state = 'HEALTHY'
    quotes_fresh = True
    detail = 'server and safety state healthy'

    if not paper or real_order:
        state = 'SAFETY_ALERT'
        detail = 'paper/real-order invariant mismatch'
    elif update_active:
        state = 'UPDATING'
        detail = 'planned update in progress'
    elif market_session:
        try:
            runtime = _get_json(LOCAL_RUNTIME)
            quotes_fresh = bool(runtime.get('quotesFresh'))
            if not bool(runtime.get('ok')) or not quotes_fresh:
                state = 'DATA_STALE'
                detail = 'server alive but runtime market data is stale'
        except Exception as exc:
            quotes_fresh = False
            state = 'DATA_STALE'
            detail = f'runtime health unavailable: {type(exc).__name__}'

    ui_version = None
    try:
        ui_version = _get_json(LOCAL_UPDATE).get('uiVersion')
    except Exception:
        pass

    return {
        'device': DEVICE_NAME,
        'state': state,
        'server': True,
        'paper': paper,
        'realOrder': real_order,
        'quotesFresh': quotes_fresh,
        'marketSession': market_session,
        'version': ui_version,
        'timestamp': now,
        'detail': detail,
    }


def _safe_public_payload(status):
    # Explicit allow-list: do not accidentally leak future account/trade fields.
    keys = ('device', 'state', 'server', 'paper', 'realOrder', 'quotesFresh',
            'marketSession', 'version', 'timestamp', 'detail')
    return {k: status.get(k) for k in keys}


def _publish_ntfy(status, is_change):
    msg = _safe_public_payload(status)
    state = str(msg.get('state') or 'UNKNOWN')
    priority = 1 if state == 'HEALTHY' and not is_change else (4 if state in {'SERVER_DOWN', 'DATA_STALE', 'SAFETY_ALERT'} else 2)
    tags = ['green_circle'] if state == 'HEALTHY' else (['warning'] if state in {'UPDATING', 'DATA_STALE'} else ['rotating_light'])
    body = json.dumps({
        'topic': topic(),
        'title': f'Stock Trader {state}',
        'message': json.dumps(msg, ensure_ascii=False, separators=(',', ':')),
        'priority': priority,
        'tags': tags,
    }, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(base_url() + '/', data=body, method='POST', headers={
        'Content-Type': 'application/json; charset=utf-8',
        'User-Agent': f'stock-trader-remote-health/{VERSION}',
    })
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as r:
        if int(getattr(r, 'status', 200)) >= 400:
            raise RuntimeError(f'ntfy HTTP {r.status}')
        r.read(65536)


def _publish_custom(status):
    url = (os.getenv('REMOTE_HEALTH_WEBHOOK_URL') or '').strip()
    if not url:
        return False
    body = json.dumps(_safe_public_payload(status), ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST', headers={
        'Content-Type': 'application/json; charset=utf-8',
        'User-Agent': f'stock-trader-remote-health/{VERSION}',
    })
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as r:
        if int(getattr(r, 'status', 200)) >= 400:
            raise RuntimeError(f'webhook HTTP {r.status}')
        r.read(65536)
    return True


def _load_state():
    try:
        obj = json.loads(STATE_FILE.read_text(encoding='utf-8'))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _save_state(data):
    try:
        _write_private(STATE_FILE, json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


def publish(status, is_change=False):
    if not ENABLED:
        return {'ok': False, 'disabled': True}
    error = None
    try:
        if not _publish_custom(status):
            _publish_ntfy(status, is_change)
        return {'ok': True}
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'[:300]
        return {'ok': False, 'error': error}
    finally:
        saved = _load_state()
        saved.update({
            'componentVersion': VERSION,
            'enabled': ENABLED,
            'lastState': status.get('state'),
            'lastLocalCheckAt': status.get('timestamp'),
            'lastPublishAttemptAt': _utc_iso(),
            'lastPublishError': error,
            'statusUrl': status_url() if not (os.getenv('REMOTE_HEALTH_WEBHOOK_URL') or '').strip() else (os.getenv('REMOTE_HEALTH_STATUS_URL') or None),
        })
        if error is None:
            saved['lastPublishOkAt'] = _utc_iso()
        _save_state(saved)


def run_once():
    status = collect_local_status()
    result = publish(status, is_change=True)
    return {'status': status, 'publish': result, 'statusUrl': status_url()}


def run_daemon():
    if not ENABLED:
        _save_state({'componentVersion': VERSION, 'enabled': False, 'lastLocalCheckAt': _utc_iso()})
        return 0
    previous = None
    last_publish = 0.0
    while True:
        started = time.time()
        status = collect_local_status()
        state = status.get('state')
        changed = previous is not None and state != previous
        due = (started - last_publish) >= HEARTBEAT_INTERVAL_SEC
        change_due = changed and (started - last_publish) >= MIN_CHANGE_PUBLISH_SEC
        if previous is None or due or change_due:
            result = publish(status, is_change=bool(changed))
            if result.get('ok'):
                last_publish = time.time()
        else:
            saved = _load_state()
            saved.update({
                'componentVersion': VERSION,
                'enabled': True,
                'lastState': state,
                'lastLocalCheckAt': status.get('timestamp'),
                'statusUrl': status_url(),
            })
            _save_state(saved)
        previous = state
        elapsed = time.time() - started
        time.sleep(max(1.0, CHECK_INTERVAL_SEC - elapsed))


def local_status():
    state = _load_state()
    state.setdefault('componentVersion', VERSION)
    state.setdefault('enabled', ENABLED)
    if ENABLED:
        state.setdefault('statusUrl', status_url())
    state['topicIsSecret'] = True
    state['offlineRule'] = f'no beacon for > {HEARTBEAT_INTERVAL_SEC * 2} sec => DEVICE_OR_NETWORK_OFFLINE'
    return state


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--daemon', action='store_true')
    p.add_argument('--once', action='store_true')
    p.add_argument('--status', action='store_true')
    args = p.parse_args(argv)
    if args.daemon:
        return run_daemon()
    if args.once:
        print(json.dumps(run_once(), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(local_status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
