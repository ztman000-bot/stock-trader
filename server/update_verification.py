"""Durable Android update receipts and a detached, stable updater supervisor.

This module is stdlib-only so a private copy can survive repository replacement.
It invokes the existing guarded updater; it never submits orders or restores DBs.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

STATE_PATH = Path.home() / '.stock-trader-update-receipt.json'
ACTIVE = {'QUEUED', 'FETCHING', 'UPDATING', 'VERIFYING'}
FAILED = {'FAILED', 'ROLLED_BACK', 'INTERRUPTED'}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def git_commit(root, ref='HEAD'):
    try:
        proc = subprocess.run(['git', 'rev-parse', ref], cwd=root, capture_output=True, text=True, timeout=10)
        sha = proc.stdout.strip()
        return sha if proc.returncode == 0 and re.fullmatch('[0-9a-f]{40}', sha) else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _read(path=STATE_PATH):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        if path.stat().st_size > 65536:
            raise ValueError('oversized receipt')
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or data.get('protocolVersion') != 1 or not data.get('requestId'):
            raise ValueError('invalid receipt')
        return data
    except (OSError, ValueError) as exc:
        return {'phase': 'INTERRUPTED', 'lastError': f'update receipt unreadable: {type(exc).__name__}'}


def _write(data, path=STATE_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.update-receipt-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        Path(name).replace(path)
    finally:
        Path(name).unlink(missing_ok=True)


def _alive(pid):
    try:
        if int(pid or 0) <= 0:
            return False
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def receipt_status(boot_commit=None, path=STATE_PATH):
    data = _read(path)
    phase = data.get('phase', 'NO_RECEIPT')
    if phase in ACTIVE:
        queued_age = time.time() - float(data.get('requestedEpoch') or 0)
        if ((data.get('supervisorPid') and not _alive(data['supervisorPid']))
                or (not data.get('supervisorPid') and queued_age > 45)):
            phase = 'INTERRUPTED'
            data['lastError'] = '업데이트 감독 프로세스가 종료되어 완료를 확인할 수 없습니다.'
    verified = (phase in {'SUCCEEDED', 'COMPLETED_WITH_WARNINGS'}
                and data.get('codeVerified') is True
                and data.get('localSafetyPassed') is True
                and bool(boot_commit) and data.get('targetSha') == boot_commit)
    return {**data, 'phase': phase, 'running': phase in ACTIVE,
            'codeVerified': bool(verified),
            'verified': bool(verified and phase == 'SUCCEEDED' and data.get('referenceVerified') is True),
            'runningCommit': boot_commit, 'launcher': 'android-termux'}


def new_request(root, server_pid, path=STATE_PATH):
    if receipt_status(path=path).get('running'):
        raise ValueError('update already running')
    data = {'protocolVersion': 1, 'requestId': uuid.uuid4().hex, 'phase': 'QUEUED',
            'requestedAt': _now(), 'requestedEpoch': time.time(), 'baseSha': git_commit(root),
            'targetSha': None, 'beforePid': int(server_pid), 'supervisorPid': None,
            'codeVerified': False, 'referenceVerified': False, 'lastError': None}
    _write(data, path)
    return data


def mark(request_id, path=STATE_PATH, **fields):
    data = _read(path)
    if data.get('requestId') != request_id:
        raise ValueError('update receipt belongs to another request')
    data.update(fields, updatedAt=_now())
    _write(data, path)
    return data


def prepare_runner(root):
    directory = Path(tempfile.mkdtemp(prefix='stock-trader-updater-'))
    try:
        shutil.copyfile(Path(__file__), directory / 'update_verification.py')
        shutil.copyfile(Path(root) / 'server/android_update.sh', directory / 'android_update.sh')
        return directory
    except Exception:
        shutil.rmtree(directory)
        raise


def _json_command(command, root):
    proc = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)
    if proc.returncode:
        raise ValueError('reference verification command failed')
    return json.loads(proc.stdout)


def verify_reference(root):
    command = [sys.executable, str(Path(root) / 'server/github_regime_reference.py')]
    sync = _json_command(command + ['sync'], root)
    if sync.get('disabled'):
        return {'ok': True, 'enabled': False, 'state': 'DISABLED_BY_CONFIGURATION'}
    if not sync.get('ok'):
        return {'ok': False, 'state': 'SYNC_FAILED', 'error': sync.get('error', 'reference sync failed')}
    status = _json_command(command + ['status'], root)
    meta = json.loads((Path(root) / 'research/regime/marcap_regime_meta.json').read_text())
    latest = status.get('latest') or {}
    ok = (status.get('ok') is True and status.get('researchOnly') is True
          and status.get('realOrderEnabled') is False and status.get('rows', 0) > 0
          and status.get('source') == meta.get('source')
          and set(latest) == set(meta['markets'])
          and all(row.get('schemaVersion') == meta['schemaVersion']
                  and str(row.get('tradeDate', '')) >= meta['lastDate'] for row in latest.values()))
    return {'ok': bool(ok), 'state': 'VERIFIED' if ok else 'SCHEMA_OR_DATE_MISMATCH',
            'rows': status.get('rows'), 'lastDate': status.get('last'),
            'expectedSchemaVersion': meta['schemaVersion'],
            'schemas': {key: value.get('schemaVersion') for key, value in latest.items()}}


def verify_runtime(target, root, before_pid):
    with urlopen('http://127.0.0.1:8000/api/system/liveness', timeout=10) as response:
        live = json.load(response)
    return bool(target and git_commit(root) == target and live.get('runningCommit') == target
                and live.get('ok') is True and live.get('safetyOk') is True
                and live.get('mode') == 'paper' and live.get('tradingEnabled') is False
                and int(live.get('pid') or 0) > 0 and int(live['pid']) != int(before_pid))


def supervise(root, script, request_id, before_pid, path=STATE_PATH):
    target = None
    try:
        mark(request_id, path, phase='FETCHING', supervisorPid=os.getpid())
        proc = subprocess.run(['git', 'fetch', '--prune', 'origin', 'main'], cwd=root,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        target = git_commit(root, 'origin/main')
        if proc.returncode or not target:
            raise ValueError('latest main could not be resolved')
        mark(request_id, path, phase='UPDATING', targetSha=target)
        # The endpoint retains its open-position guard. The unchanged updater
        # retains dirty-tree checks, Safety, backup, health checks and rollback.
        child = subprocess.run(['/data/data/com.termux/files/usr/bin/bash', str(script), str(before_pid)], cwd=root)
        if child.returncode:
            base = _read(path).get('baseSha')
            # This identifies restored code, not proof of a healthy old server.
            rolled_back = bool(base and target != base and git_commit(root) == base)
            mark(request_id, path, phase='ROLLED_BACK' if rolled_back else 'FAILED',
                 exitCode=child.returncode, finishedAt=_now(),
                 lastError='안전 업데이트 실패. 이전 버전 복구 여부와 업데이트 로그를 확인하세요.')
            return 1
        mark(request_id, path, phase='VERIFYING', exitCode=0, localSafetyPassed=True)
        if not verify_runtime(target, root, before_pid):
            raise ValueError('목표 커밋과 실행 중인 서버 또는 안전 상태가 일치하지 않습니다.')
        try:
            reference = verify_reference(root)
        except Exception as exc:
            reference = {'ok': False, 'state': 'CHECK_FAILED', 'error': type(exc).__name__}
        if not verify_runtime(target, root, before_pid):
            raise ValueError('검증 중 실행 중인 서버가 변경됐습니다.')
        mark(request_id, path, phase='SUCCEEDED' if reference['ok'] else 'COMPLETED_WITH_WARNINGS',
             codeVerified=True, referenceVerified=reference['ok'], reference=reference,
             finishedAt=_now(), lastError=None)
        return 0
    except Exception as exc:
        mark(request_id, path, phase='FAILED', codeVerified=False, finishedAt=_now(),
             lastError=f'{type(exc).__name__}: {exc}'[:300])
        return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--script', required=True)
    parser.add_argument('--request-id', required=True)
    parser.add_argument('--server-pid', type=int, required=True)
    parser.add_argument('--state', default=str(STATE_PATH))
    args = parser.parse_args()
    try:
        return supervise(Path(args.root), Path(args.script), args.request_id, args.server_pid, Path(args.state))
    finally:
        directory = Path(__file__).resolve().parent
        if directory.name.startswith('stock-trader-updater-'):
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
