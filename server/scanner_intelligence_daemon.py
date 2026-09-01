"""Automatic Scanner Intelligence research cache v0.17.6.
Runs shadow scanner comparison separately from the live scanner. Never sends orders.
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path

from collector import regular_session
from scanner_intelligence import historical_lab, scan, snapshot_stats, status as scanner_status

OUT = Path(__file__).resolve().parent / 'scanner_intelligence_latest.json'
_LOCK = threading.Lock()
_STARTED = False
_STATE = {
    'running': False, 'lastRun': None, 'lastError': None, 'phase': 'idle',
    'intervalMin': 180, 'liveSessionDeferred': True, 'researchOnly': True,
    'realOrder': False, 'liveMutation': False,
}


def _write(data):
    tmp = OUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(OUT)


def latest():
    if OUT.exists():
        try:
            return json.loads(OUT.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {
        'ok': True, 'generatedAt': None, 'summary': 'Scanner Intelligence 자동 연구 준비 중',
        'researchOnly': True, 'realOrder': False, 'liveMutation': False,
    }


def run_once():
    with _LOCK:
        if _STATE['running']:
            return {'ok': False, 'error': 'scanner research already running'}
        _STATE['running'] = True
    try:
        if regular_session():
            _STATE.update({'phase': 'live-session-deferred', 'lastError': None})
            return {'ok': True, 'deferred': True, 'reason': 'live session priority'}
        _STATE['phase'] = 'live-feature-cache'
        live = scan(20)
        _STATE['phase'] = 'historical-challenger-lab'
        hist = historical_lab(40)
        stats = snapshot_stats()
        now = datetime.now().isoformat(timespec='seconds')
        ch = hist.get('challengers') or {}
        ranked = sorted(ch.items(), key=lambda kv: (
            float((kv[1] or {}).get('hit2Pct') or 0),
            float((kv[1] or {}).get('avgEodPct') or 0),
            int((kv[1] or {}).get('samples') or 0),
        ), reverse=True)
        best_name, best = ranked[0] if ranked else ('-', {})
        summary = (
            f"Scanner Intelligence: 시점 스냅샷 {stats.get('days',0)}일/{stats.get('rows',0)}행. "
            f"09:30 Shadow 비교 선두 {best_name}: 표본 {best.get('samples',0)}건, "
            f"이후 +2% 도달 {best.get('hit2Pct',0)}%, 평균 최대상승 {best.get('avgFutureMaxPct',0)}%, "
            f"평균 EOD {best.get('avgEodPct',0)}%. Scanner 적중률은 PF가 아니며 실제 진입/청산 OOS 검증은 별도입니다."
        )
        data = {
            'ok': True, 'generatedAt': now, 'summary': summary,
            'live': live, 'historical': hist, 'snapshots': stats,
            'scannerStatus': scanner_status(),
            'safety': {'control': 'v0.8.0 LOCKED', 'researchOnly': True,
                       'liveMutation': False, 'realOrder': False, 'autoPromotion': False},
        }
        _write(data)
        _STATE.update({'lastRun': now, 'lastError': None, 'phase': 'idle'})
        return data
    except Exception as exc:
        _STATE.update({'lastError': f'{type(exc).__name__}: {exc}'[:600], 'phase': 'error'})
        return {'ok': False, 'error': _STATE['lastError']}
    finally:
        _STATE['running'] = False


def status():
    return dict(_STATE)


def _loop():
    time.sleep(45)
    while True:
        run_once()
        time.sleep(_STATE['intervalMin'] * 60)


def start():
    global _STARTED
    if _STARTED:
        return status()
    _STARTED = True
    threading.Thread(target=_loop, daemon=True, name='scanner-intelligence-research').start()
    return status()
