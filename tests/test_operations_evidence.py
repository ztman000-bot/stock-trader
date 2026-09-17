"""Behavioral operations checks using temporary DBs, files and fake transports."""
import ast
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
import db_backup as backup
import offsite_backup as offsite
import session_coverage as coverage
import update_verification as updater


def functions(filename, names, namespace):
    tree = ast.parse((ROOT / 'server' / filename).read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, 'exec'), namespace)
    return namespace


class BackupSetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = {name: self.root / name for name in backup.DATABASES}
        for name in ('market_data.db', 'research_experiments.db', 'regime_reference.db'):
            with closing(sqlite3.connect(self.paths[name])) as conn:
                conn.executescript('CREATE TABLE evidence(value); INSERT INTO evidence VALUES(42);'
                                  "CREATE TRIGGER immutable BEFORE UPDATE ON evidence "
                                  "BEGIN SELECT RAISE(ABORT, 'immutable'); END;")
        for p in (patch.object(backup, 'BACKUP_DIR', self.root / 'backups'),
                  patch.object(backup, 'source_paths', return_value=self.paths),
                  patch.object(backup, 'RETENTION', 3)):
            p.start()
            self.addCleanup(p.stop)

    def snapshot(self):
        result = backup.snapshot('test')
        self.assertTrue(result['ok'], result)
        return Path(result['bundlePath'])

    def test_uncheckpointed_wal_and_immutable_evidence_survive_archive_restore(self):
        with closing(sqlite3.connect(self.paths['market_data.db'])) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute('INSERT INTO evidence VALUES(99)')
            writer.commit()
            before = self.paths['market_data.db'].read_bytes()
            bundle = self.snapshot()
            self.assertEqual(before, self.paths['market_data.db'].read_bytes())
            archive = backup.create_archive(bundle, self.root / 'bundle.tar')
            restored = self.root / 'restored'
            result = backup.restore_bundle(archive, restored)
            self.assertFalse(result['liveDatabasesReplaced'])
            with closing(sqlite3.connect(restored / 'market_data.db')) as conn:
                self.assertEqual(conn.execute('SELECT value FROM evidence ORDER BY value').fetchall(), [(42,), (99,)])
            with closing(sqlite3.connect(restored / 'research_experiments.db')) as conn:
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'immutable'):
                    conn.execute('UPDATE evidence SET value=0')
            manifest = backup.verify_bundle(bundle)
            self.assertEqual(len(manifest['databases']), 3)
            self.assertTrue(manifest['restoreVerified'])
            self.assertIn('historical_market.db', manifest['notCreated'])
            self.assertTrue(backup.status()['latest']['coverageComplete'])

    def test_corruption_and_partial_copy_never_publish_success(self):
        bundle = self.snapshot()
        with patch.object(backup, '_snapshot_file', side_effect=OSError('disk full')):
            self.assertFalse(backup.snapshot()['ok'])
        self.assertEqual(backup._sets(), [bundle])
        file = bundle / 'regime_reference.db'
        data = bytearray(file.read_bytes())
        data[-1] ^= 1
        file.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'integrity'):
            backup.restore_bundle(bundle, self.root / 'restore-corrupt')
        self.assertFalse((self.root / 'restore-corrupt').exists())

    def test_new_database_invalidates_coverage_and_previously_present_missing_fails(self):
        self.snapshot()
        new_db = self.paths['historical_market.db']
        with closing(sqlite3.connect(new_db)) as conn:
            conn.execute('CREATE TABLE history(value)')
        self.assertFalse(backup.status()['latest']['coverageComplete'])
        self.assertEqual(backup.status()['latest']['missingDatabases'], ['historical_market.db'])
        self.snapshot()
        self.paths['research_experiments.db'].unlink()
        self.assertFalse(backup.snapshot()['ok'])
        self.assertFalse(backup.status()['latest']['coverageComplete'])

    def test_restore_refuses_existing_destination_links_and_traversal(self):
        bundle = self.snapshot()
        with self.assertRaises(ValueError):
            backup.restore_bundle(bundle, self.root)
        archive = self.root / 'unsafe.tar'
        for name, link in (('../escape', False), ('market_data.db', True)):
            with tarfile.open(archive, 'w') as tar:
                manifest = tarfile.TarInfo('manifest.json')
                manifest.size = 2
                tar.addfile(manifest, io.BytesIO(b'{}'))
                member = tarfile.TarInfo(name)
                if link:
                    member.type, member.linkname = tarfile.SYMTYPE, '/etc/passwd'
                tar.addfile(member)
            with self.assertRaises(ValueError):
                backup.restore_bundle(archive, self.root / 'unsafe-restore')
        self.assertFalse((self.root / 'unsafe-restore').exists())

    def test_restore_rehearsal_failure_keeps_last_good_set_and_retention(self):
        first = self.snapshot()
        with patch.object(backup, '_restore_test', side_effect=ValueError('restore failed')):
            self.assertFalse(backup.snapshot()['ok'])
        self.assertEqual(backup._sets(), [first])
        legacy = backup.BACKUP_DIR / 'market_data-legacy.db'
        legacy.write_bytes(b'legacy')
        for _ in range(3):
            self.snapshot()
        self.assertEqual(len(backup._sets()), 3)
        self.assertFalse(first.exists())
        self.assertTrue(legacy.exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX flock behavior')
    def test_concurrent_snapshot_is_rejected_without_partial_set(self):
        with backup._backup_lock():
            result = backup.snapshot()
        self.assertFalse(result['ok'])
        self.assertEqual(backup._sets(), [])

    def test_offsite_encrypts_entire_archive_and_cleans_plaintext_on_failure(self):
        seen = []
        def encrypt(path):
            with tarfile.open(path) as archive:
                seen.extend(archive.getnames())
            raise RuntimeError('encryption failed')
        with patch.object(offsite, 'ENABLED', True), \
                patch.object(offsite, 'configuration_status', return_value={'configured': True}), \
                patch.object(offsite, '_encrypt_snapshot', side_effect=encrypt), \
                patch.object(offsite, '_save_result'), patch.object(offsite, '_upload') as upload:
            result = offsite.run_once()
        self.assertFalse(result['ok'])
        self.assertIn('research_experiments.db', seen)
        self.assertIn('regime_reference.db', seen)
        self.assertIn('manifest.json', seen)
        upload.assert_not_called()
        self.assertEqual(list(backup.BACKUP_DIR.glob('.offsite-bundle-*')), [])

    def test_offsite_migration_retries_legacy_backup_and_rejects_http_redirect(self):
        now = datetime.now(offsite.KST)
        state = {'lastSuccessAt': now.isoformat()}
        with patch.object(offsite, '_load_state', return_value=state):
            self.assertFalse(offsite._success_today(now))
            state.update(backupSchemaVersion=2, restoreVerified=True)
            self.assertTrue(offsite._success_today(now))
        cipher = self.root / 'encrypted.bin'
        cipher.write_bytes(b'ciphertext')
        connection = Mock()
        connection.getresponse.return_value.status = 302
        with patch.object(offsite, '_target_url', return_value='https://backup.example/test'), \
                patch.object(offsite.http.client, 'HTTPSConnection', return_value=connection):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 302'):
                offsite._upload(cipher)
        connection.close.assert_called_once()


class UpdateReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'receipt.json'
        self.base, self.target = 'a' * 40, 'b' * 40
        with patch.object(updater, 'git_commit', return_value=self.base):
            self.request = updater.new_request(self.temp.name, 123, self.path)['requestId']

    def test_receipt_survives_restart_but_other_boot_or_failed_safety_never_verifies(self):
        updater.mark(self.request, self.path, phase='SUCCEEDED', targetSha=self.target,
                     codeVerified=True, localSafetyPassed=True, referenceVerified=True)
        self.assertTrue(updater.receipt_status(self.target, self.path)['verified'])
        self.assertFalse(updater.receipt_status(self.base, self.path)['verified'])
        updater.mark(self.request, self.path, localSafetyPassed=False)
        self.assertFalse(updater.receipt_status(self.target, self.path)['verified'])
        updater.mark(self.request, self.path, phase='FAILED', lastError='safety failed')
        self.assertFalse(updater.receipt_status(self.target, self.path)['verified'])

    def test_dead_supervisor_and_stale_queue_are_interrupted(self):
        updater.mark(self.request, self.path, phase='UPDATING', supervisorPid=777)
        with patch.object(updater, '_alive', return_value=False):
            status = updater.receipt_status(self.base, self.path)
        self.assertEqual(status['phase'], 'INTERRUPTED')
        self.assertFalse(status['running'])
        updater.mark(self.request, self.path, phase='QUEUED', supervisorPid=None, requestedEpoch=time.time()-60)
        self.assertEqual(updater.receipt_status(self.base, self.path)['phase'], 'INTERRUPTED')
        with self.assertRaises(ValueError):
            updater.mark('different-request', self.path, phase='SUCCEEDED')

    def supervise(self, exit_code=0, runtime=True, reference=None):
        def commit(root, ref='HEAD'):
            return self.target if ref == 'origin/main' else self.base
        with patch.object(updater, 'git_commit', side_effect=commit), \
                patch.object(updater.subprocess, 'run', side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=exit_code)]), \
                patch.object(updater, 'verify_runtime', return_value=runtime), \
                patch.object(updater, 'verify_reference', return_value=reference or {'ok': True}):
            updater.supervise(self.temp.name, 'private-script', self.request, 123, self.path)
        return updater.receipt_status(self.target, self.path)

    def test_rollback_and_wrong_running_commit_never_become_success(self):
        self.assertEqual(self.supervise(exit_code=1)['phase'], 'ROLLED_BACK')
        wrong = self.supervise(runtime=False)
        self.assertEqual(wrong['phase'], 'FAILED')
        self.assertFalse(wrong['verified'])

    def test_reference_failure_is_visible_warning_after_verified_code(self):
        result = self.supervise(reference={'ok': False, 'state': 'SYNC_FAILED'})
        self.assertEqual(result['phase'], 'COMPLETED_WITH_WARNINGS')
        self.assertTrue(result['codeVerified'])
        self.assertTrue(result['localSafetyPassed'])
        self.assertFalse(result['verified'])
        self.assertTrue(self.supervise()['verified'])

    def test_runtime_checks_actual_boot_sha_and_safety_not_just_new_pid(self):
        live = {'ok': True, 'runningCommit': self.target, 'safetyOk': True,
                'mode': 'paper', 'tradingEnabled': False, 'pid': 456}
        for changes, expected in (({}, True), ({'runningCommit': self.base}, False),
                                  ({'safetyOk': False}, False), ({'pid': 123}, False),
                                  ({'tradingEnabled': True}, False)):
            response = io.StringIO(json.dumps({**live, **changes}))
            with patch.object(updater, 'urlopen', return_value=response), \
                    patch.object(updater, 'git_commit', return_value=self.target):
                self.assertEqual(updater.verify_runtime(self.target, self.temp.name, 123), expected)

    def test_reference_checks_each_market_schema_and_date_and_explicit_disabled(self):
        meta = json.loads((ROOT / 'research/regime/marcap_regime_meta.json').read_text())
        good = {'ok': True, 'researchOnly': True, 'realOrderEnabled': False,
                'rows': 100, 'source': meta['source'], 'latest': {market: {
                    'schemaVersion': meta['schemaVersion'], 'tradeDate': meta['lastDate']
                } for market in meta['markets']}}
        with patch.object(updater, '_json_command', side_effect=[{'ok': True}, good]):
            self.assertTrue(updater.verify_reference(ROOT)['ok'])
        good['latest']['KOSDAQ']['schemaVersion'] = 1
        with patch.object(updater, '_json_command', side_effect=[{'ok': True}, good]):
            self.assertFalse(updater.verify_reference(ROOT)['ok'])
        good['latest']['KOSDAQ'].update(schemaVersion=meta['schemaVersion'], tradeDate='1995-05-02')
        with patch.object(updater, '_json_command', side_effect=[{'ok': True}, good]):
            self.assertFalse(updater.verify_reference(ROOT)['ok'])
        with patch.object(updater, '_json_command', return_value={'ok': True, 'disabled': True}):
            self.assertFalse(updater.verify_reference(ROOT)['enabled'])

    def test_private_runner_is_stable_when_checkout_changes(self):
        runner = updater.prepare_runner(ROOT)
        self.addCleanup(shutil.rmtree, runner, True)
        self.assertEqual((runner / 'android_update.sh').read_bytes(), (ROOT / 'server/android_update.sh').read_bytes())
        self.assertEqual((runner / 'update_verification.py').read_bytes(), Path(updater.__file__).read_bytes())


class SessionCoverageTests(unittest.TestCase):
    def test_holidays_weekends_and_calendar_expiry(self):
        _, sessions = coverage.load_calendar()
        for day in ('2026-06-03', '2026-07-17', '2026-09-24', '2026-09-25', '2026-10-05'):
            self.assertNotIn(day, sessions)
        weekend = coverage.calendar_window(datetime.fromisoformat('2026-09-27T12:00:00+09:00'))
        self.assertEqual(weekend['days'][-1], '2026-09-23')
        expired = coverage.calendar_window(datetime.fromisoformat('2026-11-01T12:00:00+09:00'))
        self.assertFalse(expired['ok'])
        self.assertEqual(expired['days'], [])

    def test_late_open_completed_buckets_and_duplicate_timestamps(self):
        now = datetime.fromisoformat('2026-01-02T16:00:00+09:00')
        late = coverage.session_coverage('2026-01-02', [], now)
        self.assertEqual(late['expectedSnapshots'], 66)
        self.assertTrue(late['wholeDayMissing'])
        day = '2026-09-17'
        at = datetime.fromisoformat(day+'T09:07:00+09:00')
        times = [day+'T09:00:00+09:00', day+'T09:00:31+09:00', day+'T09:04:59',
                 day+'T09:05:00+09:00', day+'T08:55:00+09:00', 'invalid']
        result = coverage.session_coverage(day, times, at)
        self.assertEqual((result['expectedSnapshots'], result['actualSnapshots']), (1, 1))
        self.assertFalse(result['wholeDayMissing'])
        self.assertEqual(coverage.session_coverage(day, times, at.replace(hour=8))['state'], 'PENDING')

    def test_entire_missing_day_is_counted_even_without_any_bar_tables(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'market.db'
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('CREATE TABLE scanner_intel_snapshots(trade_date, snapshot_at)')
                for i in range(78):
                    at = datetime.fromisoformat('2026-09-16T09:00:00+09:00') + timedelta(minutes=5*i)
                    conn.execute('INSERT INTO scanner_intel_snapshots VALUES(?,?)', ('2026-09-16', at.isoformat()))
                conn.commit()
            class Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime.fromisoformat('2026-09-17T16:00:00+09:00')
            ns = {'datetime': Clock, 'KST': coverage.KST, 'calendar_window': coverage.calendar_window,
                  'session_coverage': coverage.session_coverage, 'SNAPSHOT_MIN_COVERAGE_PCT': 95.,
                  'SNAPSHOT_EXPECTED_FULL': 78, '_conn': lambda: sqlite3.connect(path),
                  '_forward_baseline_date': lambda: '2026-09-16'}
            functions('research_data_health.py', {'_safe_table_exists', '_snapshot_coverage'}, ns)
            result = ns['_snapshot_coverage']('scanner_intel_snapshots')
            self.assertEqual(result['latestTradingDay']['date'], '2026-09-17')
            self.assertEqual(result['latestTradingDay']['coveragePct'], 0)
            self.assertEqual(result['forwardAverageCoveragePct'], 50)
            self.assertIn('2026-09-17', result['forwardWholeMissingDays'])
            self.assertFalse(result['futureDataBackfillAllowed'])
            missing_table = ns['_snapshot_coverage']('decision_intel_snapshots')
            self.assertFalse(missing_table['ok'])
            self.assertEqual(len(missing_table['wholeMissingDays']), 5)

    def test_recent_single_db_legacy_backup_is_not_fresh_verified_bundle(self):
        ns = functions('research_data_health.py', {'_backup_freshness'}, {'datetime': datetime, 'KST': coverage.KST})
        latest = {'modifiedAt': datetime.now(coverage.KST).isoformat()}
        self.assertFalse(ns['_backup_freshness']({'latest': latest})['backupFresh'])
        latest.update(coverageComplete=True, restoreVerified=True)
        self.assertTrue(ns['_backup_freshness']({'latest': latest})['backupFresh'])

    def test_calendar_unknown_blocks_healthy_grade_and_foundation(self):
        snapshot = {'ok': True, 'calendar': {'ok': False}, 'averageCoveragePct': 100,
                    'latestTradingDay': {'coveragePct': 100}}
        ns = {'time': time, 'datetime': datetime, 'KST': coverage.KST, '_LOCK': threading.RLock(),
              '_CACHE': {}, 'CACHE_SEC': 60, 'VERSION': 'test', '_STATE': {},
              'OFFICIAL_5M_AUDIT_DAYS': 30, 'OFFICIAL_5M_TARGET_PCT': 95., 'SNAPSHOT_MIN_COVERAGE_PCT': 95.,
              'five_minute_audit': lambda days: {'officialGoodPct': 100},
              'one_minute_coverage': lambda: {'bars': 10, 'completeBars': 10, 'dataReady': True},
              '_snapshot_coverage': lambda table: snapshot,
              '_outcome_label_quality': lambda: {'officialLabelPct': 100},
              '_strict_exit_readiness': lambda: {'ready': True}, '_db_file_status': lambda: {'ok': True}}
        functions('research_data_health.py', {'_pct', '_score_component', '_snapshot_score_value', 'report'}, ns)
        result = ns['report'](force=True)
        self.assertEqual(result['score'], 100)
        self.assertEqual(result['grade'], 'CALENDAR_UNKNOWN')
        self.assertFalse(result['readiness']['dataFoundationReady'])
        self.assertFalse(result['safety']['controlMutation'])


@unittest.skipUnless(shutil.which('node'), 'Node is not required on the Android phone')
class UpdateUiTests(unittest.TestCase):
    def test_ui_receipt_classification_and_network_reconnect(self):
        script = r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source=readFileSync('js/update-verification.js','utf8');
const {classifyUpdate,waitForUpdate}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const id='request-one',sha='b'.repeat(40);
const good={runningCommit:sha,update:{requestId:id,phase:'SUCCEEDED',targetSha:sha,codeVerified:true,localSafetyPassed:true,verified:true,referenceVerified:true,reference:{rows:15467}}};
assert.equal(classifyUpdate(good,id).state,'success');
assert.equal(classifyUpdate({...good,runningCommit:'a'.repeat(40)},id).state,'warning');
assert.notEqual(classifyUpdate(good,'older-request').state,'success');
for(const phase of ['FAILED','ROLLED_BACK','INTERRUPTED'])assert.equal(classifyUpdate({...good,update:{...good.update,phase}},id).state,'failed');
for(const key of ['codeVerified','localSafetyPassed','verified','referenceVerified'])assert.notEqual(classifyUpdate({...good,update:{...good.update,[key]:false}},id).state,'success');
assert.equal(classifyUpdate({pid:999,update:{requestId:id}},id).state,'waiting');
assert.equal(classifyUpdate({...good,update:{...good.update,phase:'COMPLETED_WITH_WARNINGS'}},id).state,'warning');
globalThis.sessionStorage={removeItem(){}};
let calls=0,events=[];
globalThis.fetch=async()=>{if(++calls===1)throw Error('restarting');return {ok:true,json:async()=>good}};
assert.equal((await waitForUpdate(id,(text,state)=>events.push(state),{attempts:3,delay:0})).state,'success');
assert.deepEqual(events,['waiting','success']);
globalThis.fetch=async()=>{throw Error('offline')};
assert.equal((await waitForUpdate(id,()=>{},{attempts:2,delay:0})).state,'waiting');
'''
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=ROOT,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
