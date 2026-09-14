import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import fsc_public_collector as public
import historical_auto_daemon as auto


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def envelope(items):
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
            "body": {
                "numOfRows": 1000,
                "pageNo": 1,
                "totalCount": len(items),
                "items": {"item": items},
            },
        }
    }


class FscPublicCollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "historical_market.db"

    def tearDown(self):
        self.tmp.cleanup()

    def fake_opener(self, request, timeout=20.0):
        parsed = urlparse(request.full_url)
        params = parse_qs(parsed.query)
        self.assertEqual(params.get("serviceKey"), ["TEST+KEY/="])
        if parsed.path.endswith("/getStockPriceInfo"):
            return FakeResponse(
                envelope(
                    [
                        {
                            "basDt": "20260911",
                            "srtnCd": "005930",
                            "isinCd": "KR7005930003",
                            "itmsNm": "삼성전자",
                            "mrktCtg": "KOSPI",
                            "clpr": "75500",
                            "mkp": "75000",
                            "hipr": "76000",
                            "lopr": "74500",
                            "trqu": "12345678",
                            "trPrc": "930000000000",
                            "lstgStCnt": "5969782550",
                            "mrktTotAmt": "450000000000000",
                        },
                        {
                            "basDt": "20260911",
                            "srtnCd": "035720",
                            "isinCd": "KR7035720002",
                            "itmsNm": "카카오",
                            "mrktCtg": "KOSDAQ",
                            "clpr": "50000",
                            "mkp": "49500",
                            "hipr": "51000",
                            "lopr": "49000",
                            "trqu": "1000000",
                            "trPrc": "50000000000",
                            "lstgStCnt": "400000000",
                            "mrktTotAmt": "20000000000000",
                        },
                    ]
                )
            )
        if parsed.path.endswith("/getStockMarketIndex"):
            return FakeResponse(
                envelope(
                    [
                        {
                            "basDt": "20260911",
                            "idxNm": "코스피",
                            "idxCsf": "KOSPI",
                            "clpr": "3110.5",
                            "mkp": "3100.1",
                            "hipr": "3120.0",
                            "lopr": "3080.0",
                            "trqu": "500000000",
                            "trPrc": "12000000000000",
                        },
                        {
                            "basDt": "20260911",
                            "idxNm": "코스닥",
                            "idxCsf": "KOSDAQ",
                            "clpr": "900.1",
                            "mkp": "895.0",
                            "hipr": "905.0",
                            "lopr": "890.0",
                            "trqu": "700000000",
                            "trPrc": "7000000000000",
                        },
                    ]
                )
            )
        if parsed.path.endswith("/getItemInfo"):
            return FakeResponse(
                envelope(
                    [
                        {
                            "basDt": "20260911",
                            "srtnCd": "005930",
                            "isinCd": "KR7005930003",
                            "mrktCtg": "KOSPI",
                            "itmsNm": "삼성전자",
                        },
                        {
                            "basDt": "20260911",
                            "srtnCd": "035720",
                            "isinCd": "KR7035720002",
                            "mrktCtg": "KOSDAQ",
                            "itmsNm": "카카오",
                        },
                    ]
                )
            )
        raise AssertionError(f"unexpected URL: {request.full_url}")

    def test_collect_date_imports_public_official_data_and_provenance(self):
        result = public.collect_date(
            date(2026, 9, 11),
            service_key="TEST+KEY/=",
            opener=self.fake_opener,
            path=self.db,
            interval_sec=0,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["source"], public.SOURCE)
        self.assertFalse(result["realOrderEnabled"])

        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM daily_bars WHERE source=?", (public.SOURCE,)).fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM index_daily WHERE source=?", (public.SOURCE,)).fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM symbol_history WHERE source=?", (public.SOURCE,)).fetchone()[0],
                2,
            )
            sources = {
                row[0]
                for row in conn.execute("SELECT DISTINCT source FROM data_provenance")
            }
        self.assertIn(public.SOURCE, sources)

        status = public.collector_status(self.db)
        self.assertEqual(status["officialRows"]["dailyBars"], 2)
        self.assertEqual(status["officialRows"]["indexDaily"], 2)
        self.assertTrue(status["historicalDb"]["readyForResearch"])

    def test_second_collect_is_idempotent_and_uses_cache(self):
        public.collect_date(
            date(2026, 9, 11),
            service_key="TEST+KEY/=",
            opener=self.fake_opener,
            path=self.db,
            interval_sec=0,
        )

        def should_not_call(*args, **kwargs):
            raise AssertionError("network should not be called for a complete cached date")

        second = public.collect_date(
            date(2026, 9, 11),
            service_key="TEST+KEY/=",
            opener=should_not_call,
            path=self.db,
            interval_sec=0,
        )
        self.assertEqual(second["status"], "COMPLETE")
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM index_daily").fetchone()[0], 2)

    def test_encoded_env_service_key_is_normalized_once(self):
        with patch.dict(os.environ, {"DATA_GO_KR_SERVICE_KEY": "TEST%2BKEY%2F%3D"}, clear=False):
            self.assertEqual(public._service_key(), "TEST+KEY/=")

    def test_access_error_never_leaks_service_key(self):
        secret = "VERY_SECRET_KEY_123"

        def denied(request, timeout=20.0):
            raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)

        with self.assertRaises(public.PublicDataAccessError) as ctx:
            public._request_page(
                public.STOCK_URL,
                {"basDt": "20260911", "numOfRows": 1, "pageNo": 1},
                service_key=secret,
                opener=denied,
            )
        self.assertNotIn(secret, str(ctx.exception))

    def test_publication_lag_avoids_same_day_false_no_data(self):
        kst = ZoneInfo("Asia/Seoul")
        before_update = datetime(2026, 9, 15, 10, 0, tzinfo=kst)
        after_update = datetime(2026, 9, 15, 19, 0, tzinfo=kst)
        self.assertEqual(public.latest_published_candidate(before_update), date(2026, 9, 13))
        self.assertEqual(public.latest_published_candidate(after_update), date(2026, 9, 14))

    def test_public_source_is_primary_and_paid_krx_is_opt_in(self):
        env = {
            "DATA_GO_KR_SERVICE_KEY": "PUBLIC_KEY",
            "FSC_PUBLIC_AUTO_COLLECT": "true",
            "KRX_AUTH_KEY": "PAID_KEY",
            "KRX_OFFICIAL_AUTO_COLLECT": "false",
        }
        with patch.dict(os.environ, env, clear=False):
            status = auto.source_status()
        self.assertEqual(status["primarySource"], public.SOURCE)
        self.assertTrue(status["publicOfficial"]["enabled"])
        self.assertFalse(status["paidKrxSupplement"]["enabled"])
        self.assertFalse(status["realOrderEnabled"])


if __name__ == "__main__":
    unittest.main()
