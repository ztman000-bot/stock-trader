from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def constant_value(path: str, name: str):
    tree = ast.parse(text(path), filename=path)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


class SafetyInvariantTests(unittest.TestCase):
    def test_real_order_is_hard_disabled_in_server(self):
        self.assertIs(constant_value("server/app.py", "ENABLE_TRADING"), False)
        src = text("server/app.py")
        self.assertIn("'tradingEnabled':False", src.replace(" ", ""))

    def test_android_start_fails_closed_unless_paper_and_false(self):
        src = text("server/start_android.sh")
        self.assertRegex(src, r"APP_MODE\[\[:space:\]\].*paper")
        self.assertRegex(src, r"ENABLE_TRADING\[\[:space:\]\].*false")
        self.assertIn('export APP_MODE="paper"', src)
        self.assertIn('export ENABLE_TRADING="false"', src)

    def test_celltrion_is_permanently_protected(self):
        src = text("server/collector.py")
        self.assertRegex(src, r"PROTECTED_CODES\s*=\s*\{['\"]068270['\"]\}")
        paper = text("server/paper_engine.py")
        self.assertIn("if code in PROTECTED_CODES", paper)
        self.assertIn("'action':'PROTECTED'", paper.replace(" ", ""))

    def test_control_v080_core_risk_constants_are_locked(self):
        expected = {
            "RISK_PER_TRADE": 0.0035,
            "STOP_PCT": 0.010,
            "MAX_CONSECUTIVE_LOSSES": 2,
            "MAX_OPEN_POSITIONS": 2,
            "MAX_DAILY_TRADES": 8,
            "DAILY_MAX_LOSS_PCT": 0.0075,
        }
        for name, value in expected.items():
            self.assertEqual(constant_value("server/paper_engine.py", name), value, name)

    def test_daily_lock_converts_new_entries_to_shadow_only(self):
        src = text("server/paper_engine.py").replace(" ", "")
        self.assertIn("ifstats['locked']ande['action']=='BUY_CANDIDATE':e['action']='SHADOW_ONLY'", src)
        self.assertIn("con>=MAX_CONSECUTIVE_LOSSES", src)

    def test_decision_intelligence_remains_shadow_only(self):
        src = text("server/decision_intelligence.py")
        self.assertIn("'riskScoreMode': 'SHADOW_ONLY'", src)
        compact = src.replace(" ", "")
        self.assertIn("'affectsEntry':False", compact)
        self.assertIn("'affectsExit':False", compact)
        self.assertIn("'affectsSizing':False", compact)
        self.assertIn("'autoPromotion':False", compact)

    def test_research_validation_cannot_enable_real_orders(self):
        src = text("server/robust_validation.py").replace(" ", "")
        self.assertIn("'liveRuleAutoMutation':False", src)
        self.assertIn("'realOrderEnabled':False", src)
        self.assertIn("'deploymentReady':bool(research_passandone_min.get('ready'))", src)

    def test_capital_policy_has_no_broker_or_order_dependency(self):
        src = text("server/capital_policy.py")
        self.assertIn("REINVEST_PCT=.40", src)
        self.assertIn("PROFIT_VAULT_PCT=.50", src)
        self.assertIn("RISK_RESERVE_PCT=.10", src)
        self.assertNotRegex(src.lower(), r"\bnhplug\b|broker|place_order|send_order")

    def test_update_paths_block_when_paper_position_open(self):
        src = text("server/unified_app.py")
        self.assertIn("_has_open_positions()", src)
        self.assertIn("열린 Paper 포지션이 있어 업데이트를 차단했습니다.", src)

    def test_preflight_imports_critical_validation_modules(self):
        src = text("server/preflight.py")
        for module in ("collector", "paper_engine", "decision_intelligence", "one_minute_exit_replay", "robust_validation"):
            self.assertIn(repr(module), src)


if __name__ == "__main__":
    unittest.main()
