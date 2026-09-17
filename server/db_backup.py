"""Verified WAL-safe backup sets; restores only to a new staging directory.

Each DB is consistent independently, not an atomic cross-database snapshot.
No broker imports, credentials, live DB replacement or trading operations.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
import uuid
from contextlib import contextmanager, closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'))
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv('MARKET_DB_PATH', str(BASE_DIR / 'market_data.db'))
KST = ZoneInfo('Asia/Seoul')
BACKUP_DIR = Path(os.getenv('DB_BACKUP_DIR', str(Path(DB_PATH).resolve().parent / 'backups')))
RETENTION = max(3, min(int(os.getenv('DB_BACKUP_RETENTION', '7')), 30))
DATABASES = {
    'market_data.db': 'MARKET_DB_PATH',
    'research_experiments.db': None,
    'regime_reference.db': 'REGIME_REFERENCE_DB_PATH',
    'historical_market.db': 'HISTORICAL_MARKET_DB_PATH',
    'us_market_data.db': 'US_MARKET_DB_PATH',
    'execution_simulation.db': None,
}


def source_paths():
    return {name: Path(DB_PATH if name == 'market_data.db' else
                       os.getenv(key, str(BASE_DIR / name)) if key else BASE_DIR / name).resolve()
            for name, key in DATABASES.items()}


def _readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=15)


def _quick_check(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as conn:
        rows = conn.execute('PRAGMA quick_check(1)').fetchall()
    return str(rows[0][0]) if rows else 'missing-result'


def _sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _manifest(directory):
    path = Path(directory) / 'manifest.json'
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError('invalid backup manifest')
    data = json.loads(path.read_text(encoding='utf-8'))
    entries = data.get('databases', [])
    names = [e['name'] for e in entries]
    if (data.get('schemaVersion') != 2 or not entries or len(names) != len(set(names))
            or not set(names).issubset(DATABASES) or 'market_data.db' not in names):
        raise ValueError('invalid backup inventory')
    for entry in entries:
        if (not isinstance(entry.get('bytes'), int) or entry['bytes'] <= 0
                or len(str(entry.get('sha256', ''))) != 64):
            raise ValueError('invalid backup file metadata')
    return data


def verify_bundle(directory):
    directory = Path(directory)
    data = _manifest(directory)
    expected = {'manifest.json', *(e['name'] for e in data['databases'])}
    if {p.name for p in directory.iterdir()} != expected:
        raise ValueError('backup set has missing or unexpected files')
    for entry in data['databases']:
        path = directory / entry['name']
        if (path.is_symlink() or not path.is_file() or path.stat().st_size != entry['bytes']
                or _sha256(path) != entry['sha256'] or _quick_check(path) != 'ok'):
            raise ValueError(f"backup integrity failed: {entry['name']}")
    return data


@contextmanager
def _backup_lock():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    with (BACKUP_DIR / '.backup.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            if not lock.read(1):
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _sets():
    return sorted((p for p in BACKUP_DIR.glob('backup-set-*') if p.is_dir() and not p.is_symlink()),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def _rotate():
    removed = []
    # Preserve legacy single-DB backups during migration.
    for path in _sets()[RETENTION:]:
        shutil.rmtree(path)
        removed.append(path.name)
    return removed


def _snapshot_file(source, target):
    deadline = time.monotonic() + 300
    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise TimeoutError('SQLite snapshot exceeded five minutes')
    with closing(_readonly(source)) as src, closing(sqlite3.connect(str(target), timeout=30)) as dst:
        src.backup(dst, pages=1024, progress=progress, sleep=.05)
        dst.commit()
        # Convert only the snapshot, never the source WAL database.
        dst.execute('PRAGMA journal_mode=DELETE')
    os.chmod(target, 0o600)
    if _quick_check(target) != 'ok':
        raise ValueError(f'backup quick_check failed: {target.name}')


def _restore_test(directory, entries):
    # Round-trip each standalone DB in isolation; space for one extra DB only.
    with tempfile.TemporaryDirectory(prefix='.restore-test-', dir=BACKUP_DIR) as temp:
        for entry in entries:
            target = Path(temp) / entry['name']
            shutil.copyfile(directory / entry['name'], target)
            try:
                if _sha256(target) != entry['sha256'] or _quick_check(target) != 'ok':
                    raise ValueError(f"restore test failed: {entry['name']}")
            finally:
                target.unlink(missing_ok=True)


def snapshot(reason='manual'):
    try:
        with _backup_lock():
            paths = source_paths()
            previous = _manifest(_sets()[0]) if _sets() else None
            required = {'market_data.db'} | ({e['name'] for e in previous['databases']} if previous else set())
            missing = [name for name in required if not paths[name].is_file()]
            if missing:
                raise ValueError('previously recorded/required database missing: ' + ', '.join(sorted(missing)))
            existing = {name: path for name, path in paths.items() if path.exists()}
            if len(set(existing.values())) != len(existing):
                raise ValueError('database paths overlap')
            now = datetime.now(KST)
            final = BACKUP_DIR / ('backup-set-' + now.strftime('%Y%m%d-%H%M%S-%f') + '-' + uuid.uuid4().hex[:8])
            with tempfile.TemporaryDirectory(prefix='.backup-stage-', dir=BACKUP_DIR) as staging:
                stage = Path(staging)
                entries = []
                for name, source in existing.items():
                    target = stage / name
                    _snapshot_file(source, target)
                    entries.append({'name': name, 'bytes': target.stat().st_size,
                                    'sha256': _sha256(target), 'quickCheck': 'ok',
                                    'capturedAt': datetime.now(KST).isoformat(timespec='seconds')})
                if any(path.exists() and name not in existing for name, path in paths.items()):
                    raise RuntimeError('database inventory changed during backup; retry required')
                _restore_test(stage, entries)
                manifest = {'schemaVersion': 2, 'createdAt': now.isoformat(timespec='seconds'),
                            'reason': reason, 'consistency': 'per-database-snapshot',
                            'databases': entries, 'notCreated': sorted(set(paths) - set(existing)),
                            'restoreVerified': True, 'orderAccess': False}
                with (stage / 'manifest.json').open('w', encoding='utf-8') as stream:
                    json.dump(manifest, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                verify_bundle(stage)
                stage.rename(final)
            return {'ok': True, 'reason': reason, 'createdAt': manifest['createdAt'],
                    'path': str(final / 'market_data.db'), 'bundlePath': str(final),
                    'bytes': sum(e['bytes'] for e in entries), 'quickCheck': 'ok',
                    'schemaVersion': 2, 'databases': [e['name'] for e in entries],
                    'restoreVerified': True, 'retention': RETENTION, 'removed': _rotate()}
    except Exception as exc:
        return {'ok': False, 'error': f'{type(exc).__name__}: {exc}', 'reason': reason}


def status():
    sets = _sets()
    legacy = sorted(BACKUP_DIR.glob('market_data-*.db'), key=lambda p: p.stat().st_mtime, reverse=True)
    out = {'ok': True, 'backupDir': str(BACKUP_DIR), 'retention': RETENTION,
           'count': len(sets), 'legacyCount': len(legacy), 'latest': None, 'orderAccess': False}
    if sets:
        try:
            data = _manifest(sets[0])
            entries = data['databases']
            included = {e['name'] for e in entries}
            paths = source_paths()
            required = {'market_data.db'} | {name for name, path in paths.items() if path.exists()}
            missing_sources = sorted(name for name in included if not paths[name].is_file())
            missing_files = [e['name'] for e in entries if not (sets[0] / e['name']).is_file()
                             or (sets[0] / e['name']).stat().st_size != e['bytes']]
            complete = not (required - included or missing_sources or missing_files)
            out['latest'] = {'name': sets[0].name, 'modifiedAt': data['createdAt'],
                             'bytes': sum(e['bytes'] for e in entries), 'schemaVersion': 2,
                             'databases': sorted(included), 'coverageComplete': complete,
                             'missingDatabases': sorted(required - included),
                             'missingSourceDatabases': missing_sources, 'missingBackupFiles': missing_files,
                             'restoreVerified': data.get('restoreVerified') is True,
                             'consistency': data['consistency']}
        except Exception as exc:
            out.update(ok=False, error=f'{type(exc).__name__}: {exc}')
    elif legacy:
        latest = legacy[0]
        out['latest'] = {'name': latest.name, 'bytes': latest.stat().st_size,
                         'modifiedAt': datetime.fromtimestamp(latest.stat().st_mtime, KST).isoformat(),
                         'schemaVersion': 1, 'coverageComplete': False, 'restoreVerified': False,
                         'databases': ['market_data.db']}
    return out


def create_archive(directory, target):
    data = verify_bundle(directory)
    with tarfile.open(target, 'w') as archive:
        for name in ['manifest.json', *(e['name'] for e in data['databases'])]:
            path = Path(directory) / name
            member = tarfile.TarInfo(name)
            member.size, member.mode = path.stat().st_size, 0o600
            with path.open('rb') as stream:
                archive.addfile(member, stream)
    return Path(target)


def restore_bundle(source, destination):
    """Accept a verified directory or plain tar; never overwrite any destination."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists() or destination in source_paths().values():
        raise ValueError('restore destination must be a new staging directory')
    if source.is_file():
        with tempfile.TemporaryDirectory(prefix='backup-unpack-') as temp:
            with tarfile.open(source, 'r:') as archive:
                members = archive.getmembers()
                names = [m.name for m in members]
                if ('manifest.json' not in names or len(names) > len(DATABASES) + 1
                        or len(names) != len(set(names))
                        or not set(names).issubset({'manifest.json', *DATABASES})):
                    raise ValueError('unsafe archive inventory')
                for member in members:
                    maximum = 65536 if member.name == 'manifest.json' else 16 * 1024**3
                    if not member.isfile() or member.size < 0 or member.size > maximum:
                        raise ValueError('unsafe archive member')
                    with archive.extractfile(member) as src, (Path(temp) / member.name).open('wb') as dst:
                        shutil.copyfileobj(src, dst)
            return restore_bundle(temp, destination)
    data = verify_bundle(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.restore-stage-', dir=destination.parent) as temp:
        stage = Path(temp)
        for name in ['manifest.json', *(e['name'] for e in data['databases'])]:
            shutil.copyfile(source / name, stage / name)
            os.chmod(stage / name, 0o600)
        verify_bundle(stage)
        stage.rename(destination)
    return {'ok': True, 'stagingPath': str(destination), 'databases': [e['name'] for e in data['databases']],
            'liveDatabasesReplaced': False, 'orderAccess': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--reason', default='manual')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--verify')
    parser.add_argument('--restore-from')
    parser.add_argument('--restore-to')
    args = parser.parse_args()
    try:
        if args.restore_from:
            if not args.restore_to:
                parser.error('--restore-to is required')
            result = restore_bundle(args.restore_from, args.restore_to)
        elif args.verify:
            data = verify_bundle(args.verify)
            result = {'ok': True, 'databases': [e['name'] for e in data['databases']], 'orderAccess': False}
        else:
            result = status() if args.status else snapshot(args.reason)
    except Exception as exc:
        result = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
