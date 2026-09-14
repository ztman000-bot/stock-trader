import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import krx_historical_reference as krx


class KrxHistoricalReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "historical_market.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_is_separate_and_research_only(self):
        result = krx.init_db(self.db)
        self.assertTrue(result["ok"])
        self.assertTrue(result["researchOnly"])
        self.assertEqual(krx.CONTROL_STRATEGY, "v0.8.0 LOCKED")
        self.assertFalse(krx.REAL_ORDER_ENABLED)
        self.assertTrue(self.db.exists())
        with sqlite3.connect(self.db) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"daily_bars", "index_daily", "symbol_history", "delisted_symbols", "corporate_actions", "data_provenance"}.issubset(tables))

    def test_daily_import_is_idempotent_and_keeps_adjusted_separate(self):
        rows = [
            {
                "BAS_DD": "20260911",
                "ISU_SRT_CD": "005930",
                "ISU_ABBRV": "삼성전자",
                "TDD_OPNPRC": "75,000",
                "TDD_HGPRC": "76,000",
                "TDD_LWPRC": "74,500",
                "TDD_CLSPRC": "75,500",
                "ACC_TRDVOL": "12,345,678",
                "ACC_TRDVAL": "930000000000",
                "MKTCAP": "450000000000000",
                "LIST_SHRS": "5969782550",
            }
        ]
        first = krx.import_daily_rows(rows, market="KOSPI", source="KRX_EXPORT", source_ref="sample.csv", path=self.db)
        second = krx.import_daily_rows(rows, market="KOSPI", source="KRX_EXPORT", source_ref="sample.csv", path=self.db)
        krx.import_daily_rows(rows, market="KOSPI", source="KRX_EXPORT", source_ref="adjusted.csv", adjusted=True, path=self.db)
        self.assertEqual(first["writtenRows"], 1)
        self.assertEqual(second["writtenRows"], 1)
        with sqlite3.connect(self.db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
            close = conn.execute("SELECT close FROM daily_bars WHERE adjusted=0").fetchone()[0]
        self.assertEqual(count, 2)
        self.assertEqual(close, 75500.0)

    def test_delisted_history_enables_survivorship_support(self):
        krx.import_delisted_rows(
            [
                {
                    "종목코드": "123456",
                    "종목명": "테스트상폐",
                    "시장구분": "KOSDAQ",
                    "상장일": "20100104",
                    "폐지일": "20231228",
                    "폐지사유": "테스트",
                }
            ],
            source="KRX_EXPORT",
            source_ref="delisted.csv",
            path=self.db,
        )
        report = krx.status(self.db)
        self.assertTrue(report["survivorshipSupport"])
        self.assertEqual(report["counts"]["delisted_symbols"], 1)
        self.assertFalse(report["realOrderEnabled"])

    def test_index_import_and_provenance(self):
        result = krx.import_index_rows(
            [
                {
                    "BAS_DD": "20260911",
                    "IDX_CLSS": "KOSPI",
                    "IDX_NM": "코스피",
                    "OPNPRC_IDX": "3100.1",
                    "HGPRC_IDX": "3120.0",
                    "LWPRC_IDX": "3080.0",
                    "CLSPRC_IDX": "3110.5",
                    "ACC_TRDVOL": "500000000",
                    "ACC_TRDVAL": "12000000000000",
                }
            ],
            source="KRX_EXPORT",
            source_ref="kospi-index.csv",
            market="KOSPI",
            path=self.db,
        )
        self.assertEqual(result["writtenRows"], 1)
        report = krx.status(self.db)
        self.assertTrue(report["readyForResearch"])
        self.assertEqual(report["counts"]["index_daily"], 1)
        self.assertTrue(any(p["dataset_type"] == "index_daily" for p in report["provenance"]))

    def test_invalid_rows_are_rejected_not_partially_written(self):
        result = krx.import_daily_rows(
            [
                {"BAS_DD": "bad", "ISU_SRT_CD": "005930", "TDD_CLSPRC": "100"},
                {"BAS_DD": "20260911", "ISU_SRT_CD": "ABC", "TDD_CLSPRC": "100"},
                {"BAS_DD": "20260911", "ISU_SRT_CD": "005930", "TDD_CLSPRC": "-"},
            ],
            market="KOSPI",
            path=self.db,
        )
        self.assertEqual(result["writtenRows"], 0)
        self.assertEqual(result["rejectedRows"], 3)
        self.assertEqual(krx.status(self.db)["counts"]["daily_bars"], 0)


if __name__ == "__main__":
    unittest.main()
