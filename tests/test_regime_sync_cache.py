"""Parser upgrades must reimport an unchanged HTTP resource; temporary DBs only."""
import csv
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
import github_regime_reference as regime
import marcap_regime_builder as builder


class RegimeSyncCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'reference.db'
        self.url = regime.DEFAULT_SUMMARY_URL
        for target, value in (('reference_enabled', True), ('summary_url', self.url)):
            patcher = patch.object(regime, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        rows = []
        for market in ('KOSPI', 'KOSDAQ'):
            rows.append(builder.aggregate_market(
                [dict(Volume=100, Amount=1000, Marcap=5000, ChangesRatio=1)],
                '2026-09-15', market, 'FinanceData/marcap@test:data/marcap-2026.parquet'))
        self.rows = builder.add_rolling_regime(rows)
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=builder.SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(self.rows)
        self.csv = stream.getvalue()
        regime.import_summary_text(self.csv, self.db, etag='"same-file"', url=self.url)
        self.requests = []

    def legacy(self):
        # Reproduce an old parser which ignored the extra columns in the same CSV.
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM regime_meta WHERE key IN ('importer_revision','schema_version')")
            c.execute('UPDATE regime_daily SET schema_version=NULL,equal_weight_change_pct=NULL,'
                      'active_market_cap_total=NULL,missing_return_count=NULL')

    def response(self, request, timeout):
        self.requests.append(request)
        if request.get_header('If-none-match') == '"same-file"':
            raise HTTPError(request.full_url, 304, 'Not Modified', {}, None)
        result = io.BytesIO(self.csv.encode())
        result.headers = {'ETag': '"same-file"'}
        return result

    def test_legacy_database_reimports_unchanged_resource_and_populates_v2(self):
        self.legacy()
        self.assertTrue(regime.collector_status(self.db)['reimportRequired'])
        result = regime.sync_summary(self.db, opener=self.response)
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['notModified'])
        self.assertEqual(result['schemaVersion'], 2)
        self.assertIsNone(self.requests[0].get_header('If-none-match'))
        self.assertEqual(self.requests[0].get_header('Cache-control'), 'no-cache')
        state = regime.collector_status(self.db)
        self.assertFalse(state['reimportRequired'])
        self.assertEqual(state['importedWithRevision'], regime.IMPORTER_REVISION)
        for row in state['latest'].values():
            self.assertEqual(row['schemaVersion'], 2)
            self.assertEqual(row['equalWeightChangePct'], 1)
            self.assertEqual(row['activeMarketCapTotal'], 5000)
            self.assertEqual(row['missingReturnCount'], 0)
        self.assertTrue(regime.sync_summary(self.db, opener=self.response)['notModified'])
        self.assertEqual(self.requests[-1].get_header('If-none-match'), '"same-file"')

    def test_parser_revision_and_source_url_changes_bypass_etag(self):
        with patch.object(regime, 'IMPORTER_REVISION', 'next-reviewed-parser'):
            result = regime.sync_summary(self.db, opener=self.response)
            self.assertFalse(result['notModified'])
            self.assertEqual(regime._meta('importer_revision', self.db), 'next-reviewed-parser')
        regime.import_summary_text(self.csv, self.db, etag='"same-file"', url=self.url)
        changed_url = self.url + '?reviewed=1'
        with patch.object(regime, 'summary_url', return_value=changed_url):
            self.assertFalse(regime.sync_summary(self.db, opener=self.response)['notModified'])
        self.assertEqual(regime._meta('summary_url', self.db), changed_url)
        self.assertTrue(all(r.get_header('If-none-match') is None for r in self.requests))

    def test_partial_empty_and_mixed_schema_databases_are_not_valid_http_caches(self):
        for sql in ("DELETE FROM regime_daily WHERE market='KOSDAQ'", 'DELETE FROM regime_daily',
                    "UPDATE regime_daily SET schema_version=1 WHERE market='KOSDAQ'"):
            with self.subTest(sql=sql):
                regime.import_summary_text(self.csv, self.db, etag='"same-file"', url=self.url)
                with sqlite3.connect(self.db) as c:
                    c.execute(sql)
                self.assertFalse(regime.sync_summary(self.db, opener=self.response)['notModified'])
                self.assertEqual(regime.collector_status(self.db)['rows'], 2)

    def test_force_refresh_and_unexpected_304_do_not_claim_success(self):
        self.assertFalse(regime.sync_summary(self.db, opener=self.response, force=True)['notModified'])
        def always_304(request, timeout):
            raise HTTPError(request.full_url, 304, 'Not Modified', {}, None)
        self.assertTrue(regime.sync_summary(self.db, opener=always_304)['ok'])
        self.assertFalse(regime.sync_summary(self.db, opener=always_304, force=True)['ok'])
        self.legacy()
        self.assertFalse(regime.sync_summary(self.db, opener=always_304)['ok'])
        self.assertEqual(regime.collector_status(self.db)['rows'], 2)

    def test_failed_reimport_preserves_rows_etag_and_missing_parser_marker(self):
        self.legacy()
        with sqlite3.connect(self.db) as c:
            before = c.execute('SELECT * FROM regime_daily ORDER BY market').fetchall()
        def network_error(request, timeout):
            raise URLError('offline')
        def bad_csv(request, timeout):
            result = io.BytesIO(b'invalid,header\n1,2\n')
            result.headers = {'ETag': '"bad"'}
            return result
        for opener in (network_error, bad_csv):
            result = regime.sync_summary(self.db, opener=opener)
            self.assertFalse(result['ok'])
            with sqlite3.connect(self.db) as c:
                self.assertEqual(c.execute('SELECT * FROM regime_daily ORDER BY market').fetchall(), before)
            self.assertEqual(regime._meta('etag', self.db), '"same-file"')
            self.assertIsNone(regime._meta('importer_revision', self.db))

    def test_no_change_does_not_rewrite_import_time_and_force_keeps_safety_validation(self):
        before = regime._meta('last_sync_at', self.db)
        self.assertTrue(regime.sync_summary(self.db, opener=self.response)['notModified'])
        self.assertEqual(regime._meta('last_sync_at', self.db), before)
        # Force controls HTTP cache only. It cannot bypass schema downgrade checks.
        legacy_rows = [{k: v for k, v in row.items() if k not in builder.EXTRA_FIELDS} for row in self.rows]
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=builder.SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(legacy_rows)
        self.csv = stream.getvalue()
        self.assertFalse(regime.sync_summary(self.db, opener=self.response, force=True)['ok'])
        self.assertEqual(regime.collector_status(self.db)['latest']['KOSPI']['schemaVersion'], 2)

    def test_disabled_source_does_not_fetch_or_create_database(self):
        path = Path(self.temp.name) / 'disabled.db'
        with patch.object(regime, 'reference_enabled', return_value=False):
            self.assertTrue(regime.sync_summary(path, opener=self.response, force=True)['disabled'])
        self.assertFalse(path.exists())
        self.assertEqual(self.requests, [])

    def test_cli_force_option_and_failure_exit_code(self):
        argv = ['github_regime_reference.py', 'sync', '--force', '--db', str(self.db)]
        with patch.object(sys, 'argv', argv), patch.object(regime, 'sync_summary', return_value={'ok': False}) as sync:
            with redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(regime._cli(), 1)
            sync.assert_called_once_with(str(self.db), force=True)
            self.assertFalse(json.loads(stream.getvalue())['ok'])


if __name__ == '__main__':
    unittest.main()
