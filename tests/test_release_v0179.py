from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT/path).read_text(encoding='utf-8')


class ReleaseBaselineTests(unittest.TestCase):
    def test_unified_app_compiles_and_reports_current_release(self):
        src=text('server/unified_app.py')
        ast.parse(src,filename='server/unified_app.py')
        self.assertIn("UI_VERSION='0.17.10'",src)
        self.assertIn("'version':'0.17.10'",src)

    def test_v0179_hardening_features_are_retained(self):
        src=text('server/unified_app.py')
        for marker in (
            "'runtimeQuoteFreshnessWatchdog':True",
            "'safetyInvariantCI':True",
            "'dependencyRollback':True",
            "'walSafeDbSnapshots':True",
            "'officialNh5mProvenanceGate':True",
            "'purgedNonOverlapWalkForward':True",
            "'qualityGate':'OFFICIAL_NH_GOOD_ONLY'",
        ):
            self.assertIn(marker,src)

    def test_control_version_remains_v080_and_real_order_off(self):
        app=text('server/app.py').replace(' ','')
        self.assertIn("VERSION='0.8.0'",app)
        self.assertIn('ENABLE_TRADING=False',app)

    def test_readme_publishes_current_release_and_locked_control(self):
        readme=text('README.md')
        self.assertTrue(readme.startswith('# Stock Day Trader v0.17.10'))
        self.assertIn('Control v0.8.0 LOCKED',readme)
        self.assertIn('REAL ORDER OFF',readme)


if __name__=='__main__':
    unittest.main()
