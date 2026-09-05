from __future__ import annotations

import ast
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / 'server' / 'robust_validation.py').read_text(encoding='utf-8')


def isolated_date_slices():
    tree = ast.parse(SRC, filename='server/robust_validation.py')
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_date_slices')
    module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {'ceil': math.ceil, 'WF_FOLDS': 4, 'WF_PURGE_DAYS': 1}
    exec(compile(module, 'server/robust_validation.py', 'exec'), ns)
    return ns['_date_slices']


class ValidationHardeningTests(unittest.TestCase):
    def test_robust_validation_source_compiles(self):
        ast.parse(SRC, filename='server/robust_validation.py')

    def test_walk_forward_is_purged_and_non_overlapping_by_construction(self):
        self.assertIn("WF_PURGE_DAYS=1", SRC)
        self.assertIn("test_start=min(len(dev),cursor+max(0,int(purge_days)))", SRC)
        self.assertIn("cursor=test_end", SRC)
        self.assertIn("'method':'expanding-non-overlap-purged-v2'", SRC)

    def test_date_slices_are_actually_disjoint(self):
        slicer = isolated_date_slices()
        folds, lockbox = slicer([{'date': i} for i in range(1, 41)], folds=4, purge_days=1)
        self.assertGreaterEqual(len(folds), 3)
        seen_test = set()
        for train, test in folds:
            self.assertTrue(train)
            self.assertTrue(test)
            self.assertTrue(train.isdisjoint(test))
            self.assertTrue(test.isdisjoint(lockbox))
            self.assertTrue(seen_test.isdisjoint(test))
            self.assertGreater(min(test), max(train) + 1)
            seen_test.update(test)
        all_dev = set().union(*(train | test for train, test in folds))
        self.assertTrue(all_dev.isdisjoint(lockbox))
        self.assertGreater(min(lockbox), max(seen_test))

    def test_robust_gate_is_more_conservative(self):
        self.assertIn("WF_MIN_TRAIN_TRADES=12", SRC)
        self.assertIn("LOCKBOX_MIN_TRADES=20", SRC)
        self.assertIn("len(results)>=3", SRC)
        self.assertIn("positive>=required_positive", SRC)

    def test_control_and_real_order_invariants_remain_off(self):
        compact = SRC.replace(' ', '')
        self.assertIn("'liveRuleAutoMutation':False", compact)
        self.assertIn("'realOrderEnabled':False", compact)
        self.assertIn("'deploymentReady':bool(research_passandone_min.get('ready'))", compact)


if __name__ == '__main__':
    unittest.main()
