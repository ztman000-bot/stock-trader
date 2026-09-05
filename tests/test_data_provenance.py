from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding='utf-8')


class DataProvenanceTests(unittest.TestCase):
    def test_sources_compile(self):
        for path in ('server/historical_accumulator.py','server/data_quality.py','server/profitability_lab.py'):
            ast.parse(text(path), filename=path)

    def test_historical_accumulator_marks_official_nh_bars(self):
        src=text('server/historical_accumulator.py')
        self.assertIn("OFFICIAL_SOURCE='nh_period_5m'",src)
        self.assertIn('bar_5m_provenance',src)
        self.assertIn("source=excluded.source",src)
        self.assertIn("existing>=76 and official>=76",src)

    def test_default_research_quality_requires_official_provenance(self):
        src=text('server/data_quality.py')
        self.assertIn("OFFICIAL_SOURCE='nh_period_5m'",src)
        self.assertIn('MIN_OFFICIAL_BARS=76',src)
        self.assertIn("official_ready=bool(grade=='GOOD' and official>=MIN_OFFICIAL_BARS)",src)
        self.assertIn('def structural_quality_map',src)
        self.assertIn('def research_quality_map',src)
        self.assertIn('return research_quality_map(max_days)',src)
        self.assertIn('OFFICIAL_NH_GOOD_ONLY_FOR_PROFITABILITY_RESEARCH',src)

    def test_profitability_and_robust_use_default_strict_quality_map(self):
        profitability=text('server/profitability_lab.py')
        robust=text('server/robust_validation.py')
        self.assertIn('from data_quality import quality_map',profitability)
        self.assertIn('qmap=quality_map(120)',profitability)
        self.assertIn("if qmap.get(key)!='GOOD':continue",profitability)
        self.assertIn('from profitability_lab import',robust)
        self.assertIn('_candidates',robust)

    def test_control_paper_engine_does_not_depend_on_provenance_gate(self):
        paper=text('server/paper_engine.py')
        self.assertNotIn('bar_5m_provenance',paper)
        self.assertNotIn('research_quality_map',paper)
        self.assertNotIn('quality_map(',paper)


if __name__ == '__main__':
    unittest.main()
