import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import krx_official_collector as collector


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")


class KrxOfficialCollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "historical_market.db"

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _opener(request, timeout=20):
        url = request.full_url
        bas_dd = "20260911"
        if "stk_bydd_trd" in url:
            rows = [{
                "BAS_DD": bas_dd,
                "ISU_CD": "005930",
                "ISU_NM": "삼성전자",
                "TDD_OPNPRC": "75000",
                "TDD_HGPRC": "76000",
                "TDD_LWPRC": "74500",
                "TDD_CLSPRC": "75500",
                "ACC_TRDVOL": "1000",
                "ACC_TRDVAL": "75500000",
                "MKTCAP": "450000000000000",
                "LIST_SHRS": "5969782550",
            }]
        elif "ksq_bydd_trd" in url:
            rows = [{
                "BAS_DD": bas_dd,
                "ISU_CD": "035720",
                "ISU_NM": "카카오",
                "TDD_OPNPRC": "60000",
                "TDD_HGPRC": "61000",
                "TDD_LWPRC": "59000",
                "TDD_CLSPRC": "60500",
                "ACC_TRDVOL": "2000",
                "ACC_TRDVAL": "121000000",
                "MKTCAP": "25000000000000",
                "LIST_SHRS": "400000000",
            }]
        elif "kospi_dd_trd" in url:
            rows = [
                {"BAS_DD": bas_dd, "IDX_CLSS": "대표", "IDX_NM": "코스피", "CLSPRC_IDX": "3200", "OPNPRC_IDX": "3190", "HGPRC_IDX": "3210", "LWPRC_IDX": "3180", "ACC_TRDVOL": "10", "ACC_TRDVAL": "100"},
                {"BAS_DD": bas_dd, "IDX_CLSS": "대표", "IDX_NM": "코스피200", "CLSPRC_IDX": "430", "OPNPRC_IDX": "429", "HGPRC_IDX": "432", "LWPRC_IDX": "428", "ACC_TRDVOL": "20", "ACC_TRDVAL": "200"},
            ]
        elif "kosdaq_dd_trd" in url:
            rows = [
                {"BAS_DD": bas_dd, "IDX_CLSS": "대표", "IDX_NM": "코스닥", "CLSPRC_IDX": "900", "OPNPRC_IDX": "895", "HGPRC_IDX": "905", "LWPRC_IDX": "890", "ACC_TRDVOL": "30", "ACC_TRDVAL": "300"},
                {"BAS_DD": bas_dd, "IDX_CLSS": "대표", "IDX_NM": "코스닥150", "CLSPRC_IDX": "1400", "OPNPRC_IDX": "1390", "HGPRC_IDX": "1410", "LWPRC_IDX": "1380", "ACC_TRDVOL": "40", "ACC_TRDVAL": "400"},
            ]
        elif "stk_isu_base_info" in url:
            rows = [{"ISU_SRT_CD": "005930", "ISU_ABBRV": "삼성전자", "LIST_DD": "19750611", "SECUGRP_NM": "주권"}]
        elif "ksq_isu_base_info" in url:
            rows = [{"ISU_SRT_CD": "035720", "ISU_ABBRV": "카카오", "LIST_DD": "20170710", "SECUGRP_NM": "주권"}]
        else:
            raise AssertionError(f"unexpected URL: {url}")
        return FakeResponse({"OutBlock_1": rows})

    def test_research_safety_constants(self):
        self.assertTrue(collector.RESEARCH_ONLY)
        self.assertFalse(collector.REAL_ORDER_ENABLED)
        self.assertEqual(collector.CONTROL_STRATEGY, "v0.8.0 LOCKED")
        self.assertNotIn("nhplug", collector.__dict__)

    def test_official_day_imports_equity_index_and_monthly_symbols(self):
        result = collector.collect_date(
            date(2026, 9, 11),
            auth_key="test-approved-key",
            opener=self._opener,
            path=self.db,
            include_symbols=True,
            interval_sec=0,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "COMPLETE")
        with sqlite3.connect(self.db) as conn:
            daily = conn.execute("SELECT COUNT(*) FROM daily_bars WHERE source='KRX_OPEN_API'").fetchone()[0]
            indices = conn.execute("SELECT COUNT(*) FROM index_daily WHERE source='KRX_OPEN_API'").fetchone()[0]
            symbols = conn.execute("SELECT COUNT(*) FROM symbol_history WHERE source='KRX_OPEN_API'").fetchone()[0]
            distinct_index_codes = conn.execute("SELECT COUNT(DISTINCT index_code) FROM index_daily WHERE source='KRX_OPEN_API'").fetchone()[0]
        self.assertEqual(daily, 2)
        self.assertEqual(indices, 4)
        self.assertEqual(distinct_index_codes, 4)
        self.assertEqual(symbols, 2)

    def test_same_day_is_idempotent_and_uses_cached_core_data(self):
        day = date(2026, 9, 11)
        first = collector.collect_date(day, auth_key="key", opener=self._opener, path=self.db, interval_sec=0)
        second = collector.collect_date(day, auth_key="key", opener=self._opener, path=self.db, interval_sec=0)
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM index_daily").fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM symbol_history").fetchone()[0], 2)

    def test_access_error_does_not_leak_key(self):
        secret = "super-secret-krx-key"

        def denied(request, timeout=20):
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

        with self.assertRaises(collector.KRXAccessError) as ctx:
            collector.request_rows("kospi_daily", "20260911", auth_key=secret, opener=denied)
        self.assertNotIn(secret, str(ctx.exception))
        self.assertIn("service approval", str(ctx.exception))

    def test_status_without_key_is_idle_not_live_enabled(self):
        report = collector.collector_status(self.db)
        self.assertTrue(report["researchOnly"])
        self.assertFalse(report["realOrderEnabled"])
        self.assertEqual(report["officialRows"]["dailyBars"], 0)
        self.assertEqual(report["officialRows"]["indexDaily"], 0)


if __name__ == "__main__":
    unittest.main()
