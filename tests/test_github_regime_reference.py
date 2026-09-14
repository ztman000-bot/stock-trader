import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import github_regime_reference as regime
import historical_auto_daemon as auto
import marcap_regime_builder as builder


HEADER = ",".join(builder.SUMMARY_FIELDS)
ROW1 = ",".join(
    [
        "2026-09-10", "KOSPI", "100", "60", "30", "10", "0.6", "0.3",
        "1000000", "5000000", "0.42", "1.2", "0.8", "0.57", "3.5", "1.1",
        "1.2", "1", "RISK_ON", builder.SOURCE, "FinanceData/marcap@abc:data/marcap-2026.parquet",
    ]
)
ROW2 = ",".join(
    [
        "2026-09-10", "KOSDAQ", "80", "20", "50", "10", "0.25", "0.625",
        "700000", "2000000", "0.31", "-2.4", "-1.9", "0.34", "-6.8", "2.2",
        "1.5", "-1", "RISK_OFF", builder.SOURCE, "FinanceData/marcap@abc:data/marcap-2026.parquet",
    ]
)
CSV_TEXT = HEADER + "\n" + ROW1 + "\n" + ROW2 + "\n"


class GitHubRegimeReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "regime_reference.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_summary_import_is_separate_research_only(self):
        result = regime.import_summary_text(CSV_TEXT, self.db, etag='"abc"', url=regime.DEFAULT_SUMMARY_URL)
        self.assertTrue(result["ok"])
        self.assertEqual(result["writtenRows"], 2)
        self.assertFalse(result["realOrderEnabled"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM regime_daily").fetchone()[0], 2)
            sources = {r[0] for r in conn.execute("SELECT DISTINCT source FROM regime_daily")}
        self.assertEqual(sources, {builder.SOURCE})

    def test_status_reports_latest_but_never_trading_readiness(self):
        regime.import_summary_text(CSV_TEXT, self.db)
        status = regime.collector_status(self.db)
        self.assertTrue(status["researchOnly"])
        self.assertFalse(status["realOrderEnabled"])
        self.assertFalse(status["rawPerStockStoredOnPhone"])
        self.assertEqual(status["rows"], 2)
        self.assertEqual(status["latest"]["KOSPI"]["regime"], "RISK_ON")

    def test_regime_classifier_is_observational(self):
        self.assertEqual(builder.classify_regime(-10, 0.30, 2.5), (-2, "STRESS"))
        self.assertEqual(builder.classify_regime(-4, 0.48, 1.0), (-1, "RISK_OFF"))
        self.assertEqual(builder.classify_regime(6, 0.65, 1.0), (2, "RISK_ON_STRONG"))
        self.assertEqual(builder.classify_regime(2, 0.55, 1.0), (1, "RISK_ON"))
        self.assertEqual(builder.classify_regime(0, 0.50, 1.0), (0, "NEUTRAL"))

    def test_github_reference_is_primary_and_keyless(self):
        env = {
            "MARCAP_GITHUB_REGIME_ENABLED": "true",
            "FSC_PUBLIC_AUTO_COLLECT": "false",
            "KRX_OFFICIAL_AUTO_COLLECT": "false",
            "REGIME_REFERENCE_DB_PATH": str(self.db),
        }
        with patch.dict(os.environ, env, clear=False):
            with patch.object(regime, "DB_PATH", self.db):
                status = auto.source_status()
        self.assertEqual(status["primarySource"], regime.SOURCE)
        self.assertTrue(status["githubRegimeReference"]["enabled"])
        self.assertFalse(status["githubRegimeReference"]["keyRequired"])
        self.assertFalse(status["publicOfficial"]["enabled"])
        self.assertFalse(status["paidKrxSupplement"]["enabled"])
        self.assertFalse(status["realOrderEnabled"])

    def test_runtime_modules_have_no_order_or_control_mutation_path(self):
        text = (SERVER / "github_regime_reference.py").read_text(encoding="utf-8")
        builder_text = (SERVER / "marcap_regime_builder.py").read_text(encoding="utf-8")
        forbidden = ("/api/nh/order", "paper_enter(", "ENABLE_TRADING=True", "nhplug", "paper_trades")
        for token in forbidden:
            self.assertNotIn(token, text)
            self.assertNotIn(token, builder_text)

        app = (SERVER / "app.py").read_text(encoding="utf-8")
        engine = (SERVER / "paper_engine.py").read_text(encoding="utf-8")
        self.assertIn("VERSION='0.8.0'", app)
        self.assertIn("ENABLE_TRADING=False", app)
        for token in (
            "RISK_PER_TRADE=.0035",
            "STOP_PCT=.010",
            "TRAIL_ACTIVATE_PCT=.015",
            "TRAIL_PCT=.008",
            "BREAKEVEN_ACTIVATE_PCT=.008",
            "MAX_CONSECUTIVE_LOSSES=2",
            "MAX_OPEN_POSITIONS=2",
            "MAX_DAILY_TRADES=8",
            "DAILY_MAX_LOSS_PCT=.0075",
        ):
            self.assertIn(token, engine)

    def test_workflow_never_commits_raw_upstream_files(self):
        workflow = (ROOT / ".github" / "workflows" / "marcap-regime-reference.yml").read_text(encoding="utf-8")
        self.assertIn("FinanceData/marcap", workflow)
        self.assertIn("marcap_regime_daily.csv", workflow)
        self.assertIn("! find research/regime", workflow)
        self.assertNotIn("git clone https://github.com/FinanceData/marcap", workflow)


if __name__ == "__main__":
    unittest.main()
