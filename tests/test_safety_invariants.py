from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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


def import_roots(path: str) -> set[str]:
    roots: set[str] = set()
    tree = ast.parse(text(path), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def called_names(path: str) -> set[str]:
    names: set[str] = set()
    tree = ast.parse(text(path), filename=path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


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

    def test_capital_policy_has_no_order_dependency(self):
        src = text("server/capital_policy.py")
        self.assertIn("REINVEST_PCT=.40", src)
        self.assertIn("PROFIT_VAULT_PCT=.50", src)
        self.assertIn("RISK_RESERVE_PCT=.10", src)
        self.assertNotIn("nhplug", import_roots("server/capital_policy.py"))
        forbidden = {"place_order", "send_order", "submit_order", "buy", "sell"}
        self.assertTrue(forbidden.isdisjoint(called_names("server/capital_policy.py")))

    def test_update_paths_block_when_paper_position_open(self):
        src = text("server/unified_app.py")
        self.assertIn("_has_open_positions()", src)
        self.assertIn("열린 Paper 포지션이 있어 업데이트를 차단했습니다.", src)

    def test_preflight_imports_critical_validation_modules(self):
        src = text("server/preflight.py")
        for module in ("collector", "paper_engine", "decision_intelligence", "one_minute_exit_replay", "robust_validation"):
            self.assertIn(repr(module), src)

    def test_android_watchdog_requires_runtime_freshness(self):
        src = text("server/android_watchdog_v2.sh")
        self.assertIn("/api/system/runtime-health", src)
        self.assertIn("runtime.get('quotesFresh')", src)
        self.assertIn("startup_grace", src)

    def test_android_update_runs_invariants_and_db_snapshot(self):
        src = text("server/android_update.sh")
        self.assertIn("test_safety_invariants.py", src)
        self.assertIn("db_backup.py --once --reason pre-update", src)
        self.assertIn("restore_previous_requirements", src)
        self.assertIn("pip check", src)

    def test_eod_missing_quote_is_recorded_not_silently_dropped(self):
        src = text("server/paper_engine.py")
        self.assertIn("NO_LATEST_QUOTE", src)
        self.assertIn("eod_unresolved", src)
        self.assertIn("collector.set_priority_codes([p['code'] for p in remaining])", src)

    def test_android_state_changes_are_tailscale_or_localhost_only(self):
        src = text("server/android_unified_app.py")
        self.assertIn("async def android_mutation_guard", src)
        self.assertIn("{'POST', 'PUT', 'PATCH', 'DELETE'}", src)
        self.assertIn("base._remote_allowed(request)", src)
        self.assertIn("Android mutation API: Tailscale/localhost only", src)

    def test_android_daily_backup_is_after_market_and_orderless(self):
        src = text("server/android_unified_app.py")
        self.assertIn("daily-after-market", src)
        self.assertIn("not base.regular_session(now)", src)
        self.assertIn("_start_daily_backup()", src)
        backup = text("server/db_backup.py")
        self.assertIn("sqlite3.connect", backup)
        self.assertIn("src.backup(dst", backup)
        self.assertIn("quick_check(1)", backup)
        self.assertNotIn("nhplug", import_roots("server/db_backup.py"))


if __name__ == "__main__":
    unittest.main()
